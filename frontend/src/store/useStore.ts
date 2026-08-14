import { create } from "zustand";
import type {
  Token, ProxyNode, ChainState, Stats, Sample,
  InventoryRecord, LogEntry, ViewName, WSEvent, StageName, StageData, BranchName,
} from "../types";
import { STAGE_ORDER } from "../types";

const LOG_MAX = 1000;

interface StoreState {
  /* ── 导航 ── */
  currentView: ViewName;
  setView: (v: ViewName) => void;

  /* ── 提链分支 ── */
  activeBranch: BranchName;
  setActiveBranch: (b: BranchName) => void;

  /* ── 连接 ── */
  wsStatus: "online" | "offline" | "connecting" | "error";
  setWsStatus: (s: StoreState["wsStatus"]) => void;

  /* ── 数据 ── */
  tokens: Token[];
  nodes: ProxyNode[];
  chainStates: Record<string, ChainState>;
  stats: Stats;
  latencies: number[];
  inventory: InventoryRecord[];
  inventoryLoaded: boolean;
  samples: { success: Sample[]; failure: Sample[] };
  samplesLoaded: { success: boolean; failure: boolean };
  sampleTab: "success" | "failure";
  logLines: LogEntry[];

  /* ── 批量运行 ── */
  batchRunning: boolean;
  runStartTime: number;
  selectedTokenIds: Set<string>;
  batchTotal: number;
  batchDone: number;

  /* ── 批量探测进度 ── */
  probeProgress: { done: number; total: number };

  /* ── QG 隧道池 ── */
  qgPool: { superState: string; resiState: string; defaultPool: string };

  /* ── 日志 ── */
  pushLog: (msg: string, level?: LogEntry["level"], chainId?: string) => void;
  clearLog: () => void;

  /* ── WebSocket 事件处理 ── */
  handleEvent: (evt: WSEvent) => void;

  /* ── 操作 ── */
  toggleTokenSelect: (id: string) => void;
  selectAllTokens: () => void;
  clearTokenSelection: () => void;
  setBatchRunning: (r: boolean) => void;
  setSampleTab: (t: "success" | "failure") => void;
}

const tag = () => new Date().toLocaleTimeString("zh-CN", { hour12: false });

export const useStore = create<StoreState>((set, get) => ({
  currentView: "overview",
  setView: (v) => set({ currentView: v }),

  activeBranch: "paypal",
  setActiveBranch: (b) => set({ activeBranch: b }),

  wsStatus: "offline",
  setWsStatus: (s) => set({ wsStatus: s }),

  tokens: [],
  nodes: [],
  chainStates: {},
  stats: { success: 0, failure: 0, byCountry: {}, failByCountry: {}, reasons: {}, stageMatrix: {} },
  latencies: [],
  inventory: [],
  inventoryLoaded: false,
  samples: { success: [], failure: [] },
  samplesLoaded: { success: false, failure: false },
  sampleTab: "success",
  logLines: [],

  batchRunning: false,
  runStartTime: 0,
  selectedTokenIds: new Set(),
  batchTotal: 0,
  batchDone: 0,

  probeProgress: { done: 0, total: 0 },

  qgPool: { superState: "—", resiState: "—", defaultPool: "resi" },

  pushLog: (msg, level = "info", chainId = "") =>
    set((s) => {
      const lines = [...s.logLines, { ts: tag(), msg, level, chainId }];
      if (lines.length > LOG_MAX) lines.shift();
      return { logLines: lines };
    }),

  clearLog: () => set({ logLines: [] }),

  handleEvent: (evt) => {
    const s = get();
    switch (evt.type) {
      case "sync": {
        const patch: Partial<StoreState> = {};
        if (evt.tokens) patch.tokens = evt.tokens;
        if (evt.stats) patch.stats = evt.stats;
        if (evt.chains) patch.chainStates = evt.chains;
        if (evt.nodes) patch.nodes = evt.nodes;
        if (evt.inventory) { patch.inventory = evt.inventory; patch.inventoryLoaded = true; }
        if (evt.qg_pool) patch.qgPool = evt.qg_pool;
        if (evt.latencies) patch.latencies = evt.latencies;
        if (evt.running !== undefined) {
          patch.batchRunning = evt.running;
          patch.runStartTime = evt.running ? Date.now() : 0;
        }
        set(patch);
        s.pushLog("状态已同步", "info");
        break;
      }
      case "chain_start": {
        set((st) => ({
          chainStates: {
            ...st.chainStates,
            [evt.chain_id]: {
              stages: {}, status: "running",
              email: evt.email || "", tokenSub: evt.token_sub || "",
              startTime: Date.now(), attempt: evt.attempt || 1,
              country: evt.country || "",
              linkMode: (evt.link_mode as ChainState["linkMode"]) || "",
            },
          },
        }));
        s.pushLog(`链路启动 — ${evt.email || evt.token_sub || evt.chain_id}`, "info", evt.chain_id);
        break;
      }
      case "channel_detect": {
        const cs = s.chainStates[evt.chain_id];
        if (cs) {
          set((st) => ({
            chainStates: {
              ...st.chainStates,
              [evt.chain_id]: {
                ...cs,
                channelDetect: {
                  channel: evt.channel,
                  methods: evt.methods,
                  present: evt.present,
                  country: evt.country,
                },
              },
            },
          }));
        }
        s.pushLog(
          `渠道探测: ${evt.channel} ${evt.present ? "✓ 存在" : "✗ 不存在"} (${(evt.methods || []).join(", ") || "无"})`,
          evt.present ? "ok" : "warn",
          evt.chain_id
        );
        break;
      }
      case "geo_probe": {
        const cs = s.chainStates[evt.chain_id];
        if (cs) {
          const stages = { ...cs.stages };
          const prev = stages[evt.stage as StageName] || ({
            state: "run", country: evt.country || "", tryN: 1, maxTry: 1,
          } as StageData);
          const actual = evt.actual_country || "";
          const drifted = !!actual && !!evt.country && actual !== evt.country;
          const reusedFrom = evt.reused ? (evt.from_stage || "") : "";
          stages[evt.stage as StageName] = {
            ...prev,
            actualCountry: actual,
            exitIp: evt.exit_ip || "",
            geoConfidence: Number(evt.geo_confidence ?? 0),
            drifted,
            reusedFrom,
          } as StageData;
          set((st) => ({
            chainStates: {
              ...st.chainStates,
              [evt.chain_id]: {
                ...cs,
                stages,
                actualCountry: evt.stage === "checkout" ? actual : (cs.actualCountry || actual),
                exitIp: evt.stage === "checkout" ? (evt.exit_ip || cs.exitIp) : (cs.exitIp || evt.exit_ip),
                geoConfidence: evt.stage === "checkout" ? Number(evt.geo_confidence ?? 0) : cs.geoConfidence,
              },
            },
          }));
        }
        if (evt.ok) {
          const drift = evt.actual_country && evt.country && evt.actual_country !== evt.country
            ? ` ⚠ 飘移 ${evt.country}→${evt.actual_country}` : "";
          const reuse = evt.reused ? ` [复用 ${evt.from_stage || ""} 出口]` : "";
          s.pushLog(`${evt.stage} 出口真实国家: ${evt.actual_country || "未知"} (${evt.exit_ip || evt.country})${drift}${reuse}`, "info", evt.chain_id);
        }
        break;
      }
      case "stage_try": {
        const cs = s.chainStates[evt.chain_id];
        if (cs) {
          const stages = { ...cs.stages };
          stages[evt.stage as StageName] = {
            state: "run", country: evt.country,
            tryN: evt.try_n, maxTry: evt.max_try,
          } as StageData;
          const linkMode = (evt.link_mode as ChainState["linkMode"])
            || (evt.stage === "taxes" || evt.stage === "confirm"
              ? "oaics" as const : cs.linkMode || "");
          set((st) => ({
            chainStates: { ...st.chainStates, [evt.chain_id]: { ...cs, stages, country: evt.country || cs.country, linkMode } },
          }));
        }
        s.pushLog(`${evt.stage} ▷ try ${evt.try_n}/${evt.max_try} via ${evt.country}`, "info", evt.chain_id);
        break;
      }
      case "stage_ok": {
        const cs = s.chainStates[evt.chain_id];
        if (cs) {
          const stages = { ...cs.stages };
          const prev = stages[evt.stage as StageName];
          stages[evt.stage as StageName] = { ...(prev || {}), state: "ok", country: evt.country } as StageData;
          const linkMode = (evt.link_mode as ChainState["linkMode"]) || cs.linkMode || "";
          const patch: ChainState = { ...cs, stages, linkMode };
          // 【已废弃】S0 探测段事件处理 (2026-08-14 探测段移除, 后端不再发 probe 事件; 保留兼容)
          if (evt.stage === "probe" && evt.detected) patch.detected = String(evt.detected);
          set((st) => ({
            chainStates: { ...st.chainStates, [evt.chain_id]: patch },
          }));
        }
        s.pushLog(`${evt.stage} ✓ (${evt.country})`, "ok", evt.chain_id);
        break;
      }
      case "stage_fail": {
        const cs = s.chainStates[evt.chain_id];
        if (cs) {
          const stages = { ...cs.stages };
          const prev = stages[evt.stage as StageName];
          stages[evt.stage as StageName] = { ...(prev || {}), state: "fail", country: evt.country } as StageData;
          const linkMode = (evt.link_mode as ChainState["linkMode"]) || cs.linkMode || "";
          set((st) => ({
            chainStates: { ...st.chainStates, [evt.chain_id]: { ...cs, stages, linkMode } },
          }));
        }
        s.pushLog(`${evt.stage} ✗ 最终失败 [${evt.country}]${evt.detail ? `: ${evt.detail}` : ""}`, "err", evt.chain_id);
        break;
      }
      case "chain_success": {
        const cs = s.chainStates[evt.chain_id];
        if (cs) {
          const lat = cs.startTime ? (Date.now() - cs.startTime) / 1000 : 0;
          set((st) => ({
            chainStates: {
              ...st.chainStates,
              [evt.chain_id]: {
                ...cs,
                status: "success", url: evt.paypal_approve_url,
                linkMode: (evt.link_mode as ChainState["linkMode"]) || cs.linkMode || "",
                actualCountry: evt.actual_country || cs.actualCountry || evt.country,
                exitIp: evt.exit_ip || cs.exitIp,
                geoConfidence: evt.geo_confidence ?? cs.geoConfidence,
                // 冻结耗时: 成功后不再继续计时
                elapsed: evt.elapsed != null ? Number(evt.elapsed) : lat,
                endTime: Date.now(),
              },
            },
            latencies: lat > 0 ? [...st.latencies, lat].slice(-500) : st.latencies,
            stats: {
              ...st.stats,
              success: (st.stats.success || 0) + 1,
              byCountry: {
                ...st.stats.byCountry,
                [evt.actual_country || evt.country]: (st.stats.byCountry[evt.actual_country || evt.country] || 0) + 1,
              },
            },
          }));
        }
        s.pushLog(`SUCCESS — BA URL 已获取 (${evt.actual_country || evt.country})`, "ok", evt.chain_id);
        break;
      }
      case "chain_failure": {
        const cs = s.chainStates[evt.chain_id];
        if (cs) {
          const lat = cs.startTime ? (Date.now() - cs.startTime) / 1000 : 0;
          set((st) => ({
            chainStates: {
              ...st.chainStates,
              [evt.chain_id]: {
                ...cs,
                status: "failed", reason: evt.reason_code, reasonText: evt.reason_text,
                linkMode: (evt.link_mode as ChainState["linkMode"]) || cs.linkMode || "",
                actualCountry: evt.actual_country || cs.actualCountry || evt.country,
                exitIp: evt.exit_ip || cs.exitIp,
                geoConfidence: evt.geo_confidence ?? cs.geoConfidence,
                elapsed: evt.elapsed != null ? Number(evt.elapsed) : lat,
                endTime: Date.now(),
              },
            },
            stats: {
              ...st.stats,
              failure: (st.stats.failure || 0) + 1,
              failByCountry: {
                ...st.stats.failByCountry,
                [evt.actual_country || evt.country]: (st.stats.failByCountry[evt.actual_country || evt.country] || 0) + 1,
              },
              reasons: {
                ...st.stats.reasons,
                [evt.reason_code]: (st.stats.reasons[evt.reason_code] || 0) + 1,
              },
            },
          }));
        }
        s.pushLog(`FAILED — ${evt.reason_code}: ${evt.reason_text || evt.error}`, "err", evt.chain_id);
        break;
      }
      case "batch_start": {
        // 新一轮开始: 清空上一轮残留进度, 重置批次计数
        set({
          chainStates: {},
          batchRunning: true,
          runStartTime: Date.now(),
          batchTotal: evt.total || 0,
          batchDone: 0,
        });
        s.pushLog(`批量启动 — ${evt.total || "?"} 条`, "info");
        break;
      }
      case "batch_progress": {
        set({ batchDone: evt.done || 0, batchTotal: evt.total || 0 });
        break;
      }
      case "batch_done": {
        set({ batchRunning: false, runStartTime: 0 });
        s.pushLog(`批量完成: 成功 ${evt.success} / 失败 ${evt.failure} / 耗时 ${evt.elapsed}s`, "info");
        break;
      }
      case "stats_update": {
        if (evt.stats) set({ stats: evt.stats });
        break;
      }
      case "proxy_health": {
        set({ nodes: evt.nodes || [] });
        s.pushLog(`健康检查完成，${(evt.nodes || []).length} 个节点`, "info");
        break;
      }
      case "node_started":
      case "node_stopped": {
        s.pushLog(`节点 ${evt.name} ${evt.type === "node_started" ? "已启动" : "已停止"}`, "info");
        if (evt.nodes) set({ nodes: evt.nodes });
        break;
      }
      case "token_imported": {
        if (evt.tokens) set({ tokens: evt.tokens });
        s.pushLog(`导入完成: ${evt.imported} 条, 失败 ${evt.failed}`, evt.failed > 0 ? "warn" : "ok");
        break;
      }
      case "token_status": {
        set((st) => ({
          tokens: st.tokens.map((t) => t.id === evt.token_id ? { ...t, status: evt.status } : t),
        }));
        break;
      }
      case "probe_done": {
        // 单条探测完成: 实时更新该 token 的会话类型 + 完整探测结果 (promo/paypal/token 状态)
        set((st) => ({
          tokens: st.tokens.map((t) =>
            t.id === evt.token_id
              ? {
                  ...t,
                  session_type: evt.session_type || t.session_type,
                  probe: evt.probe || t.probe,
                }
              : t
          ),
        }));
        break;
      }
      case "probe_progress": {
        set({ probeProgress: { done: evt.done || 0, total: evt.total || 0 } });
        break;
      }
      default:
        break;
    }
  },

  toggleTokenSelect: (id) =>
    set((s) => {
      const next = new Set(s.selectedTokenIds);
      if (next.has(id)) next.delete(id); else next.add(id);
      return { selectedTokenIds: next };
    }),

  selectAllTokens: () =>
    set((s) => {
      const next = new Set(s.selectedTokenIds);
      if (next.size < s.tokens.length) s.tokens.forEach((t) => next.add(t.id));
      else next.clear();
      return { selectedTokenIds: next };
    }),

  clearTokenSelection: () => set({ selectedTokenIds: new Set() }),

  setBatchRunning: (r) =>
    set({ batchRunning: r, runStartTime: r ? Date.now() : 0 }),

  setSampleTab: (t) => set({ sampleTab: t }),
}));
