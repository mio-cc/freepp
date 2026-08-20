import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { BAAuthRecord, BAAuthConfig, BAStep, SMAQuote, BAFeedItem, BABaSnap } from "../types";
import { BA_STEPS, baStepCn } from "../types";

/* ── 授权监控日志类型 (类型定义在 types, store 持有全局实例) ── */

const FEED_BADGE: Record<BAFeedItem["level"], string> = {
  ok: "badge-success",
  info: "badge-info",
  warn: "badge-warn",
  err: "badge-danger",
};

const FEED_LABEL: Record<BAFeedItem["level"], string> = {
  ok: "成功",
  info: "信息",
  warn: "警告",
  err: "失败",
};

const FEED_LEVEL_OPTIONS: { value: string; label: string }[] = [
  { value: "all", label: "全部级别" },
  { value: "ok", label: "成功" },
  { value: "info", label: "信息" },
  { value: "warn", label: "警告" },
  { value: "err", label: "失败" },
];

/** 监控日志面板最多渲染条数 (避免并发多时整页渲染卡顿) */
const FEED_MAX_DISPLAY = 200;

const CAPTCHA_LABELS: Record<string, string> = {
  iq: "IQ (reCAPTCHA Enterprise)",
  pi: "PI (hCaptcha passive)",
  none: "未触发",
  "": "—",
};

const STATUS_BADGE: Record<string, string> = {
  pending: "badge-warn",
  running: "badge-info",
  success: "badge-success",
  failed: "badge-danger",
};

/** 授权中 chip: 秒数自计时 (仅重渲染本 chip, 避免整页每秒重渲染) */
function RunningChip({ r }: { r: BAAuthRecord }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div
      className="running-chip"
      title={`${r.ba_token}\n国家 ${r.identity_country || r.country || "?"}\n${r.last_msg || ""}`}
    >
      <span className="spinner" />
      <code className="mono">{r.ba_token.slice(0, 14)}…</code>
      <span>{baStepCn(r.step)}</span>
      <span className="tag">{r.identity_country || r.country || "?"}</span>
      {r.last_msg && <span className="feed-msg">{r.last_msg}</span>}
      <span className="feed-ts">
        {Math.max(0, Math.floor((now - (r.updated_at || now)) / 1000))}s
      </span>
    </div>
  );
}

const STATUS_LABELS: Record<string, string> = {
  pending: "待授权",
  running: "授权中",
  success: "已授权",
  failed: "失败",
};

const SOURCE_LABELS: Record<string, string> = {
  chain: "提链",
  manual: "手动",
  inventory: "回填",
};

const CAPTCHA_BADGE: Record<string, string> = {
  iq: "badge-info",
  pi: "badge-accent",
  none: "badge-muted",
  "": "badge-muted",
};

export function PayPalView() {
  const pushLog = useStore((s) => s.pushLog);
  const chainStates = useStore((s) => s.chainStates);

  const [baRecords, setBaRecords] = useState<BAAuthRecord[]>([]);
  const [config, setConfig] = useState<BAAuthConfig>({
    sms_provider: "smsbower",
    sms_api_key: "",
    grizzly_api_key: "",
    sms_price: "0.05",
    sms_price_min: "0",
    sms_max_attempts: 12,
    sms_timeout: 15,
    exit_country: "BR",
    identity_country: "",
    sms_country: "",
    proxy_type: "711_sticky",
    captcha_strategy: "frontend_disable",
    buyer_mode: "elevation",
    max_retries: 3,
    max_flow_attempts: 2,
    follow_chain_country: true,
    fail_fast_geo: true,
    max_concurrent: 3,
    flow_timeout_s: 120,
  });
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [detailRecord, setDetailRecord] = useState<BAAuthRecord | null>(null);
  const [countryMeta, setCountryMeta] = useState<Record<string, { sms_supported: boolean; proxy_supported: boolean; sms_country_id: string }>>({});
  const [quotes, setQuotes] = useState<Record<string, SMAQuote[]>>({});
  const [quoteLoading, setQuoteLoading] = useState<string>("");

  // 手动导入
  const [importText, setImportText] = useState("");
  const [importCountry, setImportCountry] = useState("");
  const [importEmail, setImportEmail] = useState("");
  const [importing, setImporting] = useState(false);
  const [lastImport, setLastImport] = useState<{ imported: number; exists: number; invalid: number } | null>(null);

  // 批量管理
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");

  // 实时监控日志 (全局 store: 切换分栏/重挂载不丢)
  const baFeed = useStore((s) => s.baFeed);
  const baSnap = useStore((s) => s.baSnap);
  const pushBaFeed = useStore((s) => s.pushBaFeed);
  const clearBaFeed = useStore((s) => s.clearBaFeed);
  const setBaSnap = useStore((s) => s.setBaSnap);
  const rehydrateBaFeed = useStore((s) => s.rehydrateBaFeed);
  const baFeedRef = useRef<ReturnType<typeof useStore.getState>["baFeed"]>(baFeed);
  baFeedRef.current = baFeed;

  // 监控日志筛选 (模仿实时日志页: 级别 + 链路下拉)
  const [feedLevel, setFeedLevel] = useState<string>("all");
  const [feedToken, setFeedToken] = useState<string>("all");
  const feedStreamRef = useRef<HTMLDivElement>(null);

  const feedTokens = useMemo(() => {
    const seen = new Set<string>();
    baFeed.forEach((f) => seen.add(f.token));
    return Array.from(seen);
  }, [baFeed]);

  const filteredFeed = useMemo(() => {
    return baFeed.filter((f) => {
      if (feedLevel !== "all" && f.level !== feedLevel) return false;
      if (feedToken !== "all" && f.token !== feedToken) return false;
      return true;
    });
  }, [baFeed, feedLevel, feedToken]);

  const displayFeed = useMemo(() => filteredFeed.slice(-FEED_MAX_DISPLAY), [filteredFeed]);

  // 自动滚底 (仅筛选视图变化时)
  useEffect(() => {
    if (feedStreamRef.current) {
      feedStreamRef.current.scrollTop = feedStreamRef.current.scrollHeight;
    }
  }, [displayFeed]);

  const pendingFromChains = Object.values(chainStates).filter(
    (c) => c.status === "success" && c.url && c.url.includes("ba_token=BA-")
  );

  // 用 ref 持有最新值, 避免每次渲染产生新数组导致 fetchBaRecords 依赖变化
  // -> useEffect 无限重跑 -> 刷新按钮"刷新中/刷新"闪烁
  const pendingFromChainsRef = useRef<typeof pendingFromChains>([]);
  pendingFromChainsRef.current = pendingFromChains;
  const chainStatesRef = useRef(chainStates);
  chainStatesRef.current = chainStates;

  const fetchBaRecords = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api("/api/paypal/ba/records", "GET");
      if (res && res.records) {
        setBaRecords(res.records);
      }
    } catch {
      const mockRecords: BAAuthRecord[] = pendingFromChainsRef.current.map((c) => {
        const baMatch = c.url?.match(/ba_token=(BA-[A-Za-z0-9]+)/);
        const states = chainStatesRef.current;
        return {
          ba_token: baMatch?.[1] || "",
          email: c.email,
          approve_url: c.url || "",
          status: "pending" as const,
          step: "submit_email" as BAStep,
          country: c.country,
          chain_id: Object.keys(states).find(
            (k) => states[k] === c
          ) || "",
          captcha_type: "",
          sms_phone: "",
          error: "",
          created_at: c.startTime,
          updated_at: Date.now(),
        };
      });
      setBaRecords(mockRecords);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchBaRecords();
    // 仅挂载时拉取一次, 避免链路事件驱动下的重复请求循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 挂载时拉取后端持久化配置 (上次修改的设置), 覆盖本地初始默认值
  useEffect(() => {
    (async () => {
      try {
        const res = await api("/api/paypal/ba/config", "GET");
        if (res && res.config && typeof res.config === "object") {
          setConfig((prev) => ({ ...prev, ...res.config }));
        }
      } catch {
        /* 后端不可用时保持前端默认 */
      }
    })();
  }, []);

  // 配置变更自动保存到后端 (落盘, 下次会话自动恢复); 1s 防抖避免连续输入打爆接口
  const saveConfigTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [configSaveState, setConfigSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [showApiKey, setShowApiKey] = useState(false);
  useEffect(() => {
    if (saveConfigTimer.current) clearTimeout(saveConfigTimer.current);
    saveConfigTimer.current = setTimeout(async () => {
      setConfigSaveState("saving");
      try {
        await api("/api/paypal/ba/config", "POST", config);
        setConfigSaveState("saved");
      } catch {
        // 原实现静默吞错致用户以为已存; 现显示失败态, 用户能感知需重试
        setConfigSaveState("error");
      }
    }, 1000);
    return () => {
      if (saveConfigTimer.current) clearTimeout(saveConfigTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  // 链路成功产出新 BA 时自动刷新授权队列
  const lastFetchedPending = useRef(0);
  useEffect(() => {
    if (pendingFromChains.length > lastFetchedPending.current) {
      lastFetchedPending.current = pendingFromChains.length;
      fetchBaRecords();
    }
  }, [pendingFromChains.length, fetchBaRecords]);

  // 详情弹层打开时支持 Esc 关闭
  useEffect(() => {
    if (!detailRecord) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDetailRecord(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [detailRecord]);

  // 国家元数据 (sms/proxy 可用性)
  const loadCountryMeta = useCallback(async () => {
    try {
      const res = await api("/api/paypal/identity/countries", "GET");
      if (res && Array.isArray(res.countries)) {
        const meta: Record<string, { sms_supported: boolean; proxy_supported: boolean; sms_country_id: string }> = {};
        for (const c of res.countries as Array<{ code: string; sms_supported: boolean; proxy_supported: boolean; sms_country_id: string }>) {
          meta[c.code] = { sms_supported: c.sms_supported, proxy_supported: c.proxy_supported, sms_country_id: c.sms_country_id };
        }
        setCountryMeta(meta);
      }
    } catch {
      /* 后端不可用时保持空 */
    }
  }, []);

  useEffect(() => {
    loadCountryMeta();
  }, [loadCountryMeta]);

  // 实时监控: 3s 轮询 records, 对比前后状态生成授权日志流 (feed 在全局 store)
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const res = await api("/api/paypal/ba/records", "GET");
        if (!alive || !res || !Array.isArray(res.records)) return;
        const records = res.records as BAAuthRecord[];
        setBaRecords(records);
        // 首次 (store 无快照, 如刷新后): 重建基线, 不刷屏
        let prev = useStore.getState().baSnap;
        if (prev === null) {
          rehydrateBaFeed(records);
          prev = useStore.getState().baSnap;
        }
        const items: BAFeedItem[] = [];
        const next = new Map<string, BABaSnap>();
        for (const r of records) {
          const snap: BABaSnap = {
            status: r.status, step: r.step, error: r.error,
            source: r.source || "", last_msg: r.last_msg || "",
            last_level: r.last_level || "info",
          };
          next.set(r.ba_token, snap);
          const p = prev?.get(r.ba_token);
          // 后端 last_level (info/warn/err) 映射为 feed 级别, 不再一律 info (否则告警/错误看不见)
          const lvl = (r.last_level === "warn" ? "warn" : r.last_level === "err" ? "err" : "info") as BAFeedItem["level"];
          if (!p) {
            items.push({
              ts: Date.now(), token: r.ba_token, level: "info",
              msg: `${r.source === "manual" ? "手动导入" : "加入队列"} · 国家 ${r.country || "?"}`,
            });
            continue;
          }
          if (p.status !== r.status) {
            if (r.status === "running") {
              items.push({ ts: Date.now(), token: r.ba_token, level: "info", msg: `授权启动 · 步骤 ${baStepCn(r.step)}` });
            } else if (r.status === "success") {
              items.push({ ts: Date.now(), token: r.ba_token, level: "ok", msg: "授权成功 ✓" });
            } else if (r.status === "failed") {
              items.push({ ts: Date.now(), token: r.ba_token, level: "err", msg: `授权失败: ${r.error || "未知原因"}` });
            } else if (r.status === "pending") {
              items.push({ ts: Date.now(), token: r.ba_token, level: "warn", msg: "重新入队 (重试)" });
            }
          } else if (r.status === "running" && p.step !== r.step) {
            items.push({ ts: Date.now(), token: r.ba_token, level: lvl, msg: `步骤 → ${baStepCn(r.step)}${r.last_msg ? ` · ${r.last_msg}` : ""}` });
          } else if (r.status === "running" && r.last_msg && p.last_msg !== r.last_msg) {
            items.push({ ts: Date.now(), token: r.ba_token, level: lvl, msg: r.last_msg });
          } else if (r.status === "failed" && p.error !== r.error) {
            items.push({ ts: Date.now(), token: r.ba_token, level: "err", msg: `失败更新: ${r.error || ""}` });
          }
        }
        if (prev !== null) {
          for (const key of prev.keys()) {
            if (!next.has(key)) {
              items.push({ ts: Date.now(), token: key, level: "warn", msg: "记录已删除" });
            }
          }
        }
        setBaSnap(next);
        items.forEach((item) => pushBaFeed(item));
      } catch {
        /* 后端不可用时不报错 */
      }
    };
    tick();
    const timer = setInterval(tick, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [pushBaFeed, setBaSnap, rehydrateBaFeed]);

  const loadQuote = useCallback(
    async (cc: string) => {
      if (!cc) return;
      setQuoteLoading(cc);
      try {
        const provider =
          config.sms_provider === "grizzly" ? "grizzly" : "smsbower";
        const res = await api(
          `/api/paypal/sms/quote?country=${cc}&provider=${provider}`,
          "GET"
        );
        if (res && Array.isArray(res.quotes)) {
          setQuotes((q) => ({ ...q, [cc]: res.quotes }));
        } else {
          setQuotes((q) => ({ ...q, [cc]: [] }));
        }
      } catch {
        setQuotes((q) => ({ ...q, [cc]: [] }));
      } finally {
        setQuoteLoading("");
      }
    },
    [config.sms_provider]
  );

  const ccOptions = useCallback((): string[] => {
    const keys = Object.keys(countryMeta);
    if (keys.length > 0) return keys;
    return ["BR", "US", "GB", "AU", "DE", "JP", "TH", "NL", "VN", "BH", "AO", "AE", "CI", "TR", "KR"];
  }, [countryMeta]);

  // 授权动作: 按该记录的国家组装 config (跟随链国家)
  const buildRecordConfig = useCallback(
    (r: BAAuthRecord): BAAuthConfig => {
      const cc = (config.follow_chain_country ? r.identity_country || r.country : config.identity_country) || r.country || "BR";
      return {
        ...config,
        identity_country: cc,
        sms_country: config.sms_country || cc,
        exit_country: cc,
      };
    },
    [config]
  );

  const handleStartAuth = async (r: BAAuthRecord) => {
    const baToken = r.ba_token;
    if (!baToken) return;
    const cc = (config.follow_chain_country ? r.identity_country || r.country : config.identity_country) || r.country || "BR";
    const meta = countryMeta[cc];
    if (meta && !meta.proxy_supported) {
      pushLog(`国家 ${cc} 无可用代理 (代理池未覆盖), 建议换国`, "warn", "paypal");
    }
    // 确认弹窗: 国家 → 接码报价
    if (!quotes[cc]) loadQuote(cc);
    const q = quotes[cc];
    const quoteText =
      q && q.length > 0
        ? `${q[0].provider_id} @ $${q[0].price.toFixed(4)} (共 ${q.length} 家)`
        : q
          ? "该国无可用接码供应商, 可能置灰/换国"
          : "报价查询中…";
    const confirmText = `授权国家: ${cc}\n表单字段: 生日/国籍/证件 (按 kycFields)\n代理出口: 启动前实测校验\n接码: ${quoteText}\n\n确认以该国上下文启动授权?`;
    if (!window.confirm(confirmText)) return;
    pushLog(`BA 授权启动: ${baToken} (国家 ${cc})`, "info", "paypal");
    try {
      const res = await api("/api/paypal/ba/authorize", "POST", {
        ba_token: baToken,
        config: buildRecordConfig(r),
      });
      if (res && res.ok) {
        pushLog(`BA 授权已启动: ${baToken} (国家 ${cc})`, "ok", "paypal");
        fetchBaRecords();
      } else if (res && res.error) {
        pushLog(`BA 授权启动失败: ${res.error}`, "warn", "paypal");
      }
    } catch {
      pushLog(`BA 授权启动失败 (后端不可用): ${baToken}`, "warn", "paypal");
    }
  };

  // 批量启动 (通用: 传入目标 token 列表)
  const startBatchTokens = async (tokens: string[], label: string) => {
    if (tokens.length === 0) {
      pushLog("没有可启动的 BA 记录", "warn", "paypal");
      return;
    }
    const targets = baRecords.filter((r) => tokens.includes(r.ba_token));
    const byCountry: Record<string, number> = {};
    for (const r of targets) {
      const cc = (r.identity_country || r.country || "BR").toUpperCase();
      byCountry[cc] = (byCountry[cc] || 0) + 1;
    }
    const groupText = Object.entries(byCountry)
      .map(([cc, n]) => `${cc} ×${n}`)
      .join(" / ");
    if (!window.confirm(`${label} ${tokens.length} 条:\n${groupText}\n\n每条按各自国家上下文分发 (并发上限 ${config.max_concurrent ?? 3})。确认?`)) return;
    targets.forEach((r) => {
      const cc = (r.identity_country || r.country || "BR").toUpperCase();
      if (!quotes[cc]) loadQuote(cc);
    });
    pushLog(`${label}启动: ${tokens.length} 条 BA (${groupText})`, "info", "paypal");
    try {
      const res = await api("/api/paypal/ba/batch", "POST", {
        ba_tokens: tokens,
        config,
      });
      if (res && res.ok) {
        pushLog(`${label}已启动: ${res.started}/${res.total} 条`, "ok", "paypal");
        if (res.skipped && Object.keys(res.skipped).length > 0) {
          pushLog(`${label}跳过: ${JSON.stringify(res.skipped)}`, "warn", "paypal");
        }
        fetchBaRecords();
        setSelected(new Set());
      }
    } catch {
      pushLog(`${label}启动失败 (后端不可用)`, "warn", "paypal");
    }
  };

  const handleBatchAuth = () => {
    const pending = baRecords.filter((r) => r.status === "pending");
    startBatchTokens(pending.map((r) => r.ba_token), "批量授权");
  };

  // 手动导入 BA 链接 / 裸 token
  const [importError, setImportError] = useState("");
  const handleImport = async () => {
    const text = importText.trim();
    if (!text) return;
    setImporting(true);
    setImportError("");
    try {
      const res = await api("/api/paypal/ba/import", "POST", {
        text,
        country: importCountry || config.identity_country || "",
        email: importEmail.trim(),
        source: "manual",
      });
      if (res && res.ok) {
        const summary = {
          imported: (res.imported || []).length,
          exists: (res.exists || []).length,
          invalid: (res.invalid || []).length,
        };
        setLastImport(summary);
        pushLog(
          `手动导入: 新增 ${summary.imported} / 重复 ${summary.exists} / 无效 ${summary.invalid}`,
          summary.imported > 0 ? "ok" : "warn",
          "paypal"
        );
        setImportText("");
        setImportEmail("");
        fetchBaRecords();
      } else if (res && res.error) {
        const msg = `导入失败: ${res.error}`;
        setImportError(msg);
        pushLog(msg, "warn", "paypal");
      } else {
        const msg = `导入失败: 后端返回异常 (${JSON.stringify(res).slice(0, 120)})`;
        setImportError(msg);
        pushLog(msg, "warn", "paypal");
      }
    } catch (err) {
      const msg = `导入失败: 请求异常 (${(err as Error)?.message || "后端不可用"})`;
      setImportError(msg);
      pushLog(msg, "warn", "paypal");
    } finally {
      setImporting(false);
    }
  };

  // 重跑 (failed 重试 + success 重跑统一入口)
  const handleRetryTokens = async (tokens: string[]) => {
    const targets = baRecords.filter(
      (r) => tokens.includes(r.ba_token) && (r.status === "failed" || r.status === "success")
    );
    if (targets.length === 0) {
      pushLog("所选记录中没有可重跑的 (failed/success)", "warn", "paypal");
      return;
    }
    const hasSuccess = targets.some((r) => r.status === "success");
    const groupText = [...new Set(targets.map((r) => (r.identity_country || r.country || "BR").toUpperCase()))].join(" / ");
    if (!window.confirm(
      hasSuccess
        ? `重跑 ${targets.length} 条 BA (含已授权记录, 将消耗新接码号/新卡)\n${groupText}\n并发上限 ${config.max_concurrent ?? 3}。确认?`
        : `重试 ${targets.length} 条失败 BA (${groupText}, 并发上限 ${config.max_concurrent ?? 3})?`
    )) return;
    pushLog(`${hasSuccess ? "重跑" : "批量重试"}: ${targets.length} 条 BA (${groupText})`, "info", "paypal");
    try {
      const res = await api("/api/paypal/ba/retry", "POST", {
        ba_tokens: targets.map((r) => r.ba_token),
        config: { ...config, allow_success_retry: hasSuccess },
      });
      if (res && res.ok) {
        pushLog(`已启动: ${res.started}/${res.total} 条`, "ok", "paypal");
        if (res.skipped && Object.keys(res.skipped).length > 0) {
          pushLog(`跳过: ${JSON.stringify(res.skipped)}`, "warn", "paypal");
        }
        fetchBaRecords();
        setSelected(new Set());
      }
    } catch {
      pushLog("重跑失败 (后端不可用)", "warn", "paypal");
    }
  };

  // 批量删除
  const handleDeleteTokens = async (tokens: string[]) => {
    const targets = baRecords.filter((r) => tokens.includes(r.ba_token));
    if (targets.length === 0) return;
    const runningN = targets.filter((r) => r.status === "running").length;
    if (!window.confirm(`删除 ${targets.length} 条记录?${runningN > 0 ? `\n⚠ ${runningN} 条正在授权中, 任务仍会继续执行, 仅从队列移除` : ""}`)) return;
    try {
      const res = await api("/api/paypal/ba/delete", "POST", {
        ba_tokens: targets.map((r) => r.ba_token),
      });
      if (res && res.ok) {
        pushLog(`已删除 ${res.deleted} 条 BA 记录`, "ok", "paypal");
        fetchBaRecords();
        setSelected(new Set());
      }
    } catch {
      pushLog("删除失败 (后端不可用)", "warn", "paypal");
    }
  };

  // 清空 (failed / all)
  const handleClear = async (status: "failed" | "all") => {
    const targets = status === "failed"
      ? baRecords.filter((r) => r.status === "failed")
      : baRecords;
    const label = status === "failed" ? "全部失败记录" : "全部记录";
    if (targets.length === 0) {
      pushLog(`没有可清空的${label}`, "warn", "paypal");
      return;
    }
    if (!window.confirm(`清空${label} (${targets.length} 条)?\n此操作不可恢复 (已在授权的任务不受影响)`)) return;
    try {
      const res = await api("/api/paypal/ba/clear", "POST", { status });
      if (res && res.ok) {
        pushLog(`已清空 ${res.removed} 条记录 (${res.status})`, "ok", "paypal");
        fetchBaRecords();
        setSelected(new Set());
      }
    } catch {
      pushLog("清空失败 (后端不可用)", "warn", "paypal");
    }
  };

  const handleCopy = async (r: BAAuthRecord) => {
    try {
      await navigator.clipboard.writeText(r.approve_url || `https://www.paypal.com/agreements/approve?ba_token=${r.ba_token}`);
      pushLog(`已复制 BA 链接: ${r.ba_token}`, "ok", "paypal");
    } catch {
      pushLog(`复制失败: ${r.ba_token}`, "warn", "paypal");
    }
  };

  const searchTerm = search.trim().toLowerCase();
  const filteredRecords = baRecords.filter(
    (r) =>
      (filterStatus === "all" || r.status === filterStatus) &&
      (!searchTerm ||
        r.ba_token.toLowerCase().includes(searchTerm) ||
        r.email.toLowerCase().includes(searchTerm))
  );

  const selectedList = baRecords.filter((r) => selected.has(r.ba_token));
  const runningList = baRecords.filter((r) => r.status === "running");

  // 接码价格区间统计 (滑块轨道按平台实际价格映射 + 区间内价位升序展示)
  const smsQuoteCc = (config.sms_country || config.identity_country || "BR").toUpperCase();
  const smsQuotes = quotes[smsQuoteCc];
  const smsPrices =
    smsQuotes && smsQuotes.length > 0 ? smsQuotes.map((q) => q.price).sort((a, b) => a - b) : [];
  const smsTrackMin = smsPrices.length > 0 ? smsPrices[0] : 0;
  const smsTrackMax = smsPrices.length > 0 ? smsPrices[smsPrices.length - 1] : 0.5;
  const smsMin = parseFloat(config.sms_price_min || "0") || 0;
  const smsMax = parseFloat(config.sms_price) || 0; // 0 = 不限
  const smsInRange =
    smsPrices.length > 0
      ? smsPrices.filter((p) => p >= smsMin && (smsMax > 0 ? p <= smsMax : true))
      : [];
  const smsInRangeCount = smsInRange.length;

  // 轨道百分比 <-> 价格映射 (0~100 整数, 由滑块原生拖动)
  const priceToV = (p: number) => {
    if (smsTrackMax <= smsTrackMin) return 100;
    return Math.round(((p - smsTrackMin) / (smsTrackMax - smsTrackMin)) * 100);
  };
  const vToPrice = (v: number) => smsTrackMin + (v / 100) * (smsTrackMax - smsTrackMin);
  const fmtPrice = (p: number) => String(parseFloat(p.toFixed(4)));

  const sliderMinV = smsMin <= 0 ? 0 : Math.min(100, priceToV(smsMin));
  const sliderMaxV = smsMax <= 0 ? 100 : Math.min(100, priceToV(smsMax));

  useEffect(() => {
    if (smsQuoteCc && !quotes[smsQuoteCc]) loadQuote(smsQuoteCc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [smsQuoteCc]);

  // 勾选逻辑
  const toggleSelect = (tok: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tok)) next.delete(tok);
      else next.add(tok);
      return next;
    });
  };

  const allVisibleSelected =
    filteredRecords.length > 0 && filteredRecords.every((r) => selected.has(r.ba_token));

  const toggleSelectAll = () => {
    setSelected(
      allVisibleSelected ? new Set() : new Set(filteredRecords.map((r) => r.ba_token))
    );
  };

  const stats = {
    total: baRecords.length,
    pending: baRecords.filter((r) => r.status === "pending").length,
    running: baRecords.filter((r) => r.status === "running").length,
    success: baRecords.filter((r) => r.status === "success").length,
    failed: baRecords.filter((r) => r.status === "failed").length,
  };

  const successRate =
    stats.total > 0
      ? ((stats.success / (stats.success + stats.failed || 1)) * 100).toFixed(0)
      : "—";

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h2 className="page-title">PayPal 支付授权</h2>
          <p className="page-sub">
            PayPal BA (Billing Agreement) 授权流程 — 提链段完成后独立执行的支付授权
          </p>
        </div>
        <div className="page-actions">
          <button
            className="btn"
            onClick={fetchBaRecords}
            disabled={loading}
            style={{ minWidth: 78 }}
          >
            {loading ? "刷新中…" : "刷新"}
          </button>
          <button
            className="btn btn-primary"
            onClick={handleBatchAuth}
            disabled={stats.pending === 0}
          >
            批量授权 ({stats.pending})
          </button>
        </div>
      </div>

      {/* 紧凑统计条: 单行, 不占大块空间 */}
      <div
        className="card"
        style={{ marginBottom: 14, padding: "8px 14px", display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}
      >
        <span className="tag">BA 总数 <b style={{ marginLeft: 4 }}>{stats.total}</b></span>
        <span className="tag" style={{ color: "var(--warn)" }}>待授权 {stats.pending}</span>
        <span className="tag" style={{ color: "var(--info)" }}>授权中 {stats.running}</span>
        <span className="tag" style={{ color: "var(--ok)" }}>已授权 {stats.success}</span>
        <span className="tag" style={{ color: "var(--danger)" }}>失败 {stats.failed}</span>
        <span className="tag">成功率 {successRate}%</span>
        <div style={{ flex: 1 }} />
        <span className="card-hint">3s 自动刷新 · 实时步骤见下方监控流</span>
      </div>

      {/* BA 授权实时监控 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">BA 授权实时监控</span>
          <span className="card-hint">3s 轮询 · 实时步骤/取号/OTP/授权结果</span>
          <div style={{ flex: 1 }} />
          <select
            className="select"
            style={{ width: 110 }}
            value={feedLevel}
            onChange={(e) => setFeedLevel(e.target.value)}
            title="按日志级别筛选"
          >
            {FEED_LEVEL_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <select
            className="select"
            style={{ width: 130 }}
            value={feedToken}
            onChange={(e) => setFeedToken(e.target.value)}
            title="按 BA 链路筛选"
          >
            <option value="all">全部链路</option>
            {feedTokens.map((tok) => (
              <option key={tok} value={tok}>
                {tok.slice(0, 12)}…
              </option>
            ))}
          </select>
          <span className="tag">运行中 {stats.running}</span>
          <button className="btn btn-sm btn-ghost" onClick={() => clearBaFeed()}>
            清空日志
          </button>
        </div>
        <div className="card-body">
          {runningList.length > 0 && (
            <div className="running-strip">
              {runningList.map((r) => (
                <RunningChip key={r.ba_token} r={r} />
              ))}
            </div>
          )}
          {displayFeed.length === 0 ? (
            <div className="feed-empty">
              {baFeed.length === 0
                ? "暂无授权日志 — 队列有状态变化时 (导入/启动/步骤/取号/OTP/成功/失败/删除) 实时显示在此"
                : "当前筛选条件下无日志"}
            </div>
          ) : (
            <div className="log-panel" style={{ maxHeight: 340 }}>
              <div className="log-body" ref={feedStreamRef}>
                {displayFeed.map((f, i) => (
                  <div className={`log-line ${f.level}`} key={`${f.ts}-${i}`}>
                    <span className="log-ts">
                      {new Date(f.ts).toLocaleTimeString("zh-CN", { hour12: false })}
                    </span>
                    <span
                      className="log-chain"
                      title={`点击筛选此链路: ${f.token}`}
                      style={{ cursor: "pointer", textDecoration: "underline dotted" }}
                      onClick={() => {
                        setFeedToken(f.token);
                        setFeedLevel("all");
                      }}
                    >
                      {f.token.slice(0, 8)}
                    </span>
                    <span className="log-msg">{f.msg}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 手动导入面板 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">手动导入 BA</span>
          <span className="card-hint">粘贴 paypal.com/agreements/approve 链接或裸 BA-xxx token, 每行一条 (逗号/空格分隔亦可)</span>
        </div>
        <div className="card-body">
          <textarea
            className="textarea"
            rows={4}
            placeholder={"https://www.paypal.com/agreements/approve?ba_token=BA-xxxxxxxx\nBA-xxxxxxxx\nBA-yyyyyyyy, https://…approve?ba_token=BA-zzzzzzzz"}
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            style={{ width: "100%", resize: "vertical" }}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
            <select
              className="select"
              value={importCountry}
              onChange={(e) => setImportCountry(e.target.value)}
              title="导入记录的默认国家 (授权时仍可按配置跟随)"
            >
              <option value="">国家: 跟随提链出口国家</option>
              {ccOptions().map((cc) => {
                const meta = countryMeta[cc];
                const disabled = meta ? !meta.sms_supported || !meta.proxy_supported : false;
                return (
                  <option key={cc} value={cc} disabled={disabled}>
                    {cc}
                    {meta && !meta.proxy_supported ? " (无代理)" : ""}
                    {meta && !meta.sms_supported ? " (无接码)" : ""}
                  </option>
                );
              })}
            </select>
            <input
              className="input"
              style={{ width: 220 }}
              placeholder="邮箱 (可选)"
              value={importEmail}
              onChange={(e) => setImportEmail(e.target.value)}
            />
            <button
              className="btn btn-primary"
              onClick={handleImport}
              disabled={importing || !importText.trim()}
              style={{ minWidth: 108 }}
            >
              {importing ? "导入中…" : "导入到队列"}
            </button>
            {lastImport && (
              <span className="setting-hint">
                上次导入: 新增 {lastImport.imported} / 重复 {lastImport.exists} / 无效 {lastImport.invalid}
              </span>
            )}
            {importError && (
              <span style={{ color: "var(--danger)", fontSize: 12 }}>{importError}</span>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-main">
        <div className="card">
          <div className="card-head">
            <span className="card-title">BA 授权队列</span>
            <div className="tabs">
              {["all", "pending", "running", "success", "failed"].map((f) => (
                <button
                  key={f}
                  className={`tab ${filterStatus === f ? "active" : ""}`}
                  onClick={() => setFilterStatus(f)}
                >
                  {f === "all" ? "全部" : STATUS_LABELS[f] || f}
                  {f === "all" ? ` (${baRecords.length})` : ` (${baRecords.filter((r) => r.status === f).length})`}
                </button>
              ))}
            </div>
          </div>

          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              padding: "8px 12px",
              borderBottom: "1px solid var(--border-faint)",
              flexWrap: "wrap",
            }}
          >
            <input
              className="input"
              style={{ flex: 1, minWidth: 180, maxWidth: 320 }}
              placeholder="搜索 BA Token / 邮箱…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <span style={{ flex: 1 }} />
            <button
              className="btn btn-sm"
              onClick={() => handleClear("failed")}
              disabled={stats.failed === 0}
            >
              清空失败 ({stats.failed})
            </button>
            <button
              className="btn btn-sm btn-danger"
              onClick={() => handleClear("all")}
              disabled={baRecords.length === 0}
            >
              清空全部
            </button>
          </div>

          {/* 批量操作条 */}
          {selected.size > 0 && (
            <div
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                padding: "8px 12px",
                borderBottom: "1px solid var(--border-faint)",
                background: "var(--bg-raised)",
                flexWrap: "wrap",
              }}
            >
              <span className="tag">已选 {selected.size}</span>
              <button
                className="btn btn-sm btn-primary"
                onClick={() =>
                  startBatchTokens(
                    selectedList.filter((r) => r.status === "pending").map((r) => r.ba_token),
                    "授权所选"
                  )
                }
                disabled={selectedList.every((r) => r.status !== "pending")}
              >
                授权所选
              </button>
              <button
                className="btn btn-sm"
                onClick={() => handleRetryTokens([...selected])}
                disabled={selectedList.every((r) => r.status !== "failed" && r.status !== "success")}
              >
                重跑所选
              </button>
              <button
                className="btn btn-sm btn-danger"
                onClick={() => handleDeleteTokens([...selected])}
              >
                删除所选
              </button>
              <button className="btn btn-sm btn-ghost" onClick={() => setSelected(new Set())}>
                取消选择
              </button>
            </div>
          )}

          {filteredRecords.length === 0 ? (
            <div className="empty">
              <div className="empty-icon">💳</div>
              <div className="empty-title">
                {pendingFromChains.length === 0 && !importText.trim()
                  ? "暂无 BA 记录 — 提链成功后自动导入, 或在上方手动粘贴 BA 链接"
                  : "暂无匹配记录"}
              </div>
            </div>
          ) : (
            <div className="table-wrap" style={{ border: "none", borderRadius: 0, borderTop: "1px solid var(--border-faint)" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: 32, textAlign: "center" }}>
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={toggleSelectAll}
                        onClick={(e) => e.stopPropagation()}
                        style={{ accentColor: "var(--accent)" }}
                      />
                    </th>
                    <th>BA Token</th>
                    <th>邮箱</th>
                    <th>状态</th>
                    <th>当前步骤</th>
                    <th>Captcha</th>
                    <th>国家</th>
                    <th>表单国家</th>
                    <th>接码价/号</th>
                    <th>来源</th>
                    <th style={{ textAlign: "right" }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRecords.map((r) => (
                    <tr
                      key={r.ba_token}
                      className={selected.has(r.ba_token) ? "row-selected" : ""}
                      style={{ cursor: "pointer" }}
                      onClick={() => setDetailRecord(r)}
                    >
                      <td style={{ textAlign: "center" }}>
                        <input
                          type="checkbox"
                          checked={selected.has(r.ba_token)}
                          onChange={() => toggleSelect(r.ba_token)}
                          onClick={(e) => e.stopPropagation()}
                          style={{ accentColor: "var(--accent)" }}
                        />
                      </td>
                      <td>
                        <code className="mono">{r.ba_token.slice(0, 16)}…</code>
                      </td>
                      <td>{r.email || "—"}</td>
                      <td>
                        <span className={`badge ${STATUS_BADGE[r.status] || "badge-muted"}`}>
                          {STATUS_LABELS[r.status]}
                        </span>
                      </td>
                      <td>
                        <span className="tag">{baStepCn(r.step)}</span>
                      </td>
                      <td>
                        <span className={`badge ${CAPTCHA_BADGE[r.captcha_type] || "badge-muted"}`}>
                          {r.captcha_type?.toUpperCase() || "—"}
                        </span>
                      </td>
                      <td>
                        <span className="tag">{r.country || "—"}</span>
                      </td>
                      <td>
                        <span className="tag">{r.identity_country || r.country || "—"}</span>
                      </td>
                      <td>
                        {r.sms_price ? (
                          <span className="tag" title={`provider ${r.sms_provider_id || "?"} · ${r.sms_phone || "无号"}`}>
                            ${Number(r.sms_price).toFixed(4)}
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-faint)" }}>—</span>
                        )}
                      </td>
                      <td>
                        <span className="tag">{SOURCE_LABELS[r.source || "chain"] || r.source || "—"}</span>
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        {r.status === "pending" && (
                          <button
                            className="btn btn-sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleStartAuth(r);
                            }}
                          >
                            授权
                          </button>
                        )}
                        {(r.status === "failed" || r.status === "success") && (
                          <button
                            className="btn btn-sm"
                            title={
                              r.status === "success"
                                ? "重跑将消耗新号/新卡, 用于订阅未生效等场景"
                                : "重新走完整授权流程"
                            }
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRetryTokens([r.ba_token]);
                            }}
                          >
                            重跑
                          </button>
                        )}
                        {r.status === "running" && <span className="spinner" />}
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCopy(r);
                          }}
                        >
                          复制
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteTokens([r.ba_token]);
                          }}
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">授权配置</span>
            <span
              className="setting-hint"
              style={{ marginLeft: "auto" }}
              data-save-state={configSaveState}
            >
              {configSaveState === "saving" && "保存中…"}
              {configSaveState === "saved" && "已保存 ✓"}
              {configSaveState === "error" && "保存失败 ✕"}
            </span>
          </div>
          <div className="card-body">
            {/* ── 接码设置 ── */}
            <div className="section-head">
              <span className="section-title">接码设置</span>
            </div>
            <div className="setting-row">
              <span className="setting-label">接码平台</span>
              <div className="setting-control">
                <select
                  className="select"
                  value={config.sms_provider}
                  onChange={(e) => {
                    setConfig({ ...config, sms_provider: e.target.value });
                    setQuotes({}); // 切换平台后清缓存, 报价按新平台重取
                  }}
                >
                  <option value="smsbower">SMSBower (已接入)</option>
                  <option value="grizzly">GrizzlySMS (已接入)</option>
                  <option value="sms_activate" disabled>SMS-Activate (未接入)</option>
                  <option value="5sim" disabled>5SIM (未接入)</option>
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">
                {config.sms_provider === "grizzly" ? "GrizzlySMS API Key" : "接码平台 API Key"}
              </span>
              <div className="setting-control">
                <input
                  className="input"
                  type={showApiKey ? "text" : "password"}
                  value={
                    (config.sms_provider === "grizzly"
                      ? config.grizzly_api_key
                      : config.sms_api_key) || ""
                  }
                  placeholder={
                    config.sms_provider === "grizzly"
                      ? "留空使用 .env 的 GRIZZLYSMS_API_KEY"
                      : "留空使用 backend/ba_paypal/.env 中的 key"
                  }
                  onChange={(e) =>
                    setConfig(
                      config.sms_provider === "grizzly"
                        ? { ...config, grizzly_api_key: e.target.value }
                        : { ...config, sms_api_key: e.target.value }
                    )
                  }
                  style={{ width: 260 }}
                />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowApiKey((v) => !v)}
                  aria-label={showApiKey ? "隐藏密钥" : "显示密钥"}
                  title={showApiKey ? "隐藏密钥" : "显示密钥"}
                  style={{ marginLeft: 6 }}
                >
                  {showApiKey ? "🙈" : "👁"}
                </button>
                <span className="setting-hint">保存在前端 config, 授权时覆盖 .env (仅本次会话)</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">接码价格区间 (USD/号)</span>
              <div className="setting-control">
                <div className="range-dual-wrap">
                  <div className="range-dual">
                    <div className="range-dual-track" />
                    <div
                      className="range-dual-fill"
                      style={{ left: `${sliderMinV}%`, width: `${Math.max(0, sliderMaxV - sliderMinV)}%` }}
                    />
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={1}
                      className="range-dual-input range-dual-min"
                      value={sliderMinV}
                      title="区间下限: 低于此价的供应商不取号 (默认 0 = 不限)"
                      onChange={(e) => {
                        const v = Math.min(Number(e.target.value), sliderMaxV);
                        setConfig({
                          ...config,
                          sms_price_min: v <= 0 ? "0" : fmtPrice(vToPrice(v)),
                        });
                      }}
                    />
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={1}
                      className="range-dual-input range-dual-max"
                      value={sliderMaxV}
                      title="区间上限: 高于此价的供应商不取号 (拖到最右 = 不限)"
                      onChange={(e) => {
                        const v = Math.max(Number(e.target.value), sliderMinV);
                        setConfig({
                          ...config,
                          sms_price: v >= 100 ? "0" : fmtPrice(vToPrice(v)),
                        });
                      }}
                    />
                  </div>
                  <div className="range-dual-labels">
                    <span className="range-dual-val">
                      下限 {smsMin > 0 ? `$${fmtPrice(smsMin)}` : "$0"}
                    </span>
                    <span className="range-dual-val">
                      上限 {smsMax > 0 ? `$${fmtPrice(smsMax)}` : "∞ 不限"}
                    </span>
                  </div>
                </div>
                <span className="setting-hint">
                  {smsPrices.length > 0
                    ? `按平台实际价格升序取号: 区间内 ${smsInRangeCount} 家可用 · ${smsInRange
                        .slice(0, 5)
                        .map((p) => `$${p.toFixed(4)}`)
                        .join(" / ")}${smsInRange.length > 5 ? " …" : ""} (${smsQuoteCc})`
                    : `报价查询中 (${smsQuoteCc})…`}
                </span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">换号超时 (秒)</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  min={5}
                  value={config.sms_timeout}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      sms_timeout: parseInt(e.target.value) || 15,
                    })
                  }
                />
                <span className="setting-hint">单号等待验证码超时后换下一号</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">取号重试轮数</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  min={1}
                  value={config.sms_max_attempts ?? 12}
                  title="区间内供应商全失败后冷却 2s 重试整轮, 达到轮数才放弃"
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      sms_max_attempts: Math.max(1, parseInt(e.target.value) || 12),
                    })
                  }
                />
                <span className="setting-hint">每轮 = 区间内全部价位升序试一遍; 失败冷却 2s 再下一轮</span>
              </div>
            </div>

            {/* ── 国家与代理 ── */}
            <div className="section-head" style={{ marginTop: 12 }}>
              <span className="section-title">国家与代理</span>
            </div>
            <div className="setting-row">
              <span className="setting-label">跟随提链国家</span>
              <div className="setting-control">
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={config.follow_chain_country !== false}
                    onChange={(e) =>
                      setConfig({ ...config, follow_chain_country: e.target.checked })
                    }
                  />
                  <span className={`toggle-slider ${config.follow_chain_country !== false ? "on" : ""}`} />
                </label>
                <span className="setting-hint">
                  {config.follow_chain_country !== false
                    ? "授权国家 = 提链 checkout 段出口 IP 国家"
                    : "使用下方手动国家"}
                </span>
              </div>
            </div>
            {config.follow_chain_country === false && (
              <div className="setting-row">
                <span className="setting-label">出口国家</span>
                <div className="setting-control">
                  <select
                    className="select"
                    value={config.identity_country || "BR"}
                    onChange={(e) => {
                      const cc = e.target.value;
                      setConfig({
                        ...config,
                        identity_country: cc,
                        exit_country: cc,
                      });
                      if (!quotes[cc]) loadQuote(cc);
                    }}
                  >
                    {ccOptions().map((cc) => {
                      const meta = countryMeta[cc];
                      const disabled = meta ? !meta.sms_supported || !meta.proxy_supported : false;
                      return (
                        <option key={cc} value={cc} disabled={disabled}>
                          {cc}
                          {meta && !meta.proxy_supported ? " (无代理)" : ""}
                          {meta && !meta.sms_supported ? " (无接码)" : ""}
                        </option>
                      );
                    })}
                  </select>
                  {quoteLoading === (config.identity_country || "BR") && <span className="spinner" style={{ marginLeft: 8 }} />}
                  {quotes[config.identity_country || "BR"] && (
                    <span className="setting-hint">
                      {quotes[config.identity_country || "BR"].length > 0
                        ? `最低 ${quotes[config.identity_country || "BR"][0].provider_id} $${quotes[config.identity_country || "BR"][0].price.toFixed(4)}`
                        : "该国无接码供应商"}
                    </span>
                  )}
                </div>
              </div>
            )}
            <div className="setting-row">
              <span className="setting-label">接码国家</span>
              <div className="setting-control">
                <select
                  className="select"
                  value={config.sms_country || config.identity_country || "BR"}
                  onChange={(e) =>
                    setConfig({ ...config, sms_country: e.target.value })
                  }
                >
                  {ccOptions().map((cc) => (
                    <option key={cc} value={cc}>{cc}</option>
                  ))}
                </select>
                <span className="setting-hint">
                  {config.sms_country
                    ? "手动指定接码国 (号码国家码)"
                    : "默认跟随出口国家"}
                </span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">代理类型</span>
              <div className="setting-control">
                <select
                  className="select"
                  value={config.proxy_type}
                  onChange={(e) =>
                    setConfig({ ...config, proxy_type: e.target.value })
                  }
                >
                  <option value="711_sticky">711 住宅代理 (Sticky) — 默认</option>
                  <option value="singbox">sing-box 节点优先</option>
                  <option value="qg">QG 隧道优先</option>
                </select>
                <span className="setting-hint">授权段出口代理来源; 与提链段独立选择</span>
              </div>
            </div>

            {/* ── 授权流程 ── */}
            <div className="section-head" style={{ marginTop: 12 }}>
              <span className="section-title">授权流程</span>
            </div>
            <div className="setting-row">
              <span className="setting-label">并发上限</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  min={1}
                  value={config.max_concurrent ?? 3}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      max_concurrent: parseInt(e.target.value) || 3,
                    })
                  }
                />
                <span className="setting-hint">授权段独立信号量 (提链段不受影响)</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">geo 校验失败即停</span>
              <div className="setting-control">
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={config.fail_fast_geo !== false}
                    onChange={(e) =>
                      setConfig({ ...config, fail_fast_geo: e.target.checked })
                    }
                  />
                  <span className={`toggle-slider ${config.fail_fast_geo !== false ? "on" : ""}`} />
                </label>
                <span className="setting-hint">启动前实测代理出口国家, 不一致不入流程</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">Captcha 策略</span>
              <div className="setting-control">
                <select
                  className="select"
                  value={config.captcha_strategy}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      captcha_strategy: e.target.value,
                    })
                  }
                >
                  <option value="frontend_disable">frontend_disable (本地绕过, 8/11 成功路径)</option>
                  <option value="manual_required">manual_required (人工验证)</option>
                </select>
                <span className="setting-hint">8/11 = 历史成功率; frontend_disable 无感绕过</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">最大重试 (卡/流程)</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  min={0}
                  value={config.max_retries}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      max_retries: parseInt(e.target.value) || 3,
                    })
                  }
                />
                <span className="setting-hint">单流程内换卡/换流的授权重试次数</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">最大流程尝试</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  min={1}
                  value={config.max_flow_attempts ?? 2}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      max_flow_attempts: parseInt(e.target.value) || 2,
                    })
                  }
                />
                <span className="setting-hint">BUYER_NOT_SET 后整流程重建重跑的次数</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">流程超时 (秒)</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  min={30}
                  step={10}
                  value={config.flow_timeout_s ?? 120}
                  title="单条授权流程最长耗时, 超时强制失败收尾 (默认 120s)"
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      flow_timeout_s: parseInt(e.target.value) || 120,
                    })
                  }
                  style={{ width: 84 }}
                />
                <span className="setting-hint">超时强制收尾, 防授权卡死占用并发</span>
              </div>
            </div>
          </div>

          <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
            <div className="section-head">
              <span className="section-title">授权链路</span>
            </div>
            <div className="flow-chain" style={{ borderBottom: "none", padding: "4px 0 0" }}>
              <span className="flow-node">Stripe confirm</span>
              <span className="flow-arrow">→</span>
              <span className="flow-node">pm-redirects/authorize</span>
              <span className="flow-arrow">→</span>
              <span className="flow-node accent">PayPal BA</span>
              <span className="flow-arrow">→</span>
              <span className="flow-node">EUAT</span>
            </div>
          </div>
        </div>
      </div>

      {/* 详情弹层 */}
      {detailRecord && (
        <div className="overlay" onClick={() => setDetailRecord(null)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <span className="sheet-title">BA 授权详情</span>
              <button className="icon-btn" onClick={() => setDetailRecord(null)} aria-label="关闭">✕</button>
            </div>
            <div className="sheet-body">
              <div className="detail-list">
                <div
                  className="detail-row"
                  style={{ cursor: "pointer" }}
                  title="点击跳转: 监控流筛选此链路 + 队列表格定位该记录"
                  onClick={() => {
                    setDetailRecord(null);
                    setFeedToken(detailRecord.ba_token);
                    setFeedLevel("all");
                    setFilterStatus("all");
                    setSearch(detailRecord.ba_token);
                  }}
                >
                  <span className="dr-label">BA Token</span>
                  <span className="dr-value" style={{ color: "var(--accent-strong)" }}>
                    {detailRecord.ba_token} <span style={{ fontSize: 11, opacity: 0.6 }}>→ 筛选定位</span>
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">邮箱</span>
                  <span className="dr-value">{detailRecord.email || "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">授权 URL</span>
                  <span className="dr-value" style={{ color: "var(--accent-strong)" }}>
                    {detailRecord.approve_url}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">状态</span>
                  <span>
                    <span className={`badge ${STATUS_BADGE[detailRecord.status] || "badge-muted"}`}>
                      {STATUS_LABELS[detailRecord.status]}
                    </span>
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">当前步骤</span>
                  <span className="dr-value">{baStepCn(detailRecord.step)}</span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">Captcha 类型</span>
                  <span className="dr-value">
                    {CAPTCHA_LABELS[detailRecord.captcha_type] || "—"}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">来源</span>
                  <span className="dr-value">
                    {SOURCE_LABELS[detailRecord.source || "chain"] || detailRecord.source || "—"}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">出口国家</span>
                  <span className="dr-value">{detailRecord.country || "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">表单国家</span>
                  <span className="dr-value">{detailRecord.identity_country || detailRecord.country || "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">代理出口实测</span>
                  <span className="dr-value">
                    {detailRecord.geo_country || detailRecord.proxy_country || "—"}
                    {detailRecord.geo_country && detailRecord.geo_country !== (detailRecord.identity_country || detailRecord.country) ? " ⚠ 不一致" : ""}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">来源链路</span>
                  <span className="dr-value">{detailRecord.chain_id || "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">SMS 号码</span>
                  <span className="dr-value">{detailRecord.sms_phone || "—"}</span>
                </div>
                {detailRecord.sms_price ? (
                  <div className="detail-row">
                    <span className="dr-label">接码价 (USD)</span>
                    <span className="dr-value">
                      ${Number(detailRecord.sms_price).toFixed(4)}
                      {detailRecord.sms_provider_id ? ` · provider ${detailRecord.sms_provider_id}` : ""}
                    </span>
                  </div>
                ) : null}
                {detailRecord.last_msg && (
                  <div className="detail-row">
                    <span className="dr-label">最近进度</span>
                    <span className="dr-value" style={{ color: detailRecord.last_level === "err" ? "var(--danger)" : undefined }}>
                      {detailRecord.last_msg}
                    </span>
                  </div>
                )}
                {detailRecord.error && (
                  <div className="detail-row">
                    <span className="dr-label">错误信息</span>
                    <span className="dr-value" style={{ color: "var(--danger)" }}>
                      {detailRecord.error}
                    </span>
                  </div>
                )}
              </div>
            </div>
            <div className="ba-progress" style={{ borderTop: "1px solid var(--border-faint)" }}>
              {BA_STEPS.map((step) => {
                const stepIdx = BA_STEPS.indexOf(detailRecord.step);
                const curIdx = BA_STEPS.indexOf(step);
                const isDone = curIdx < stepIdx;
                const isCurrent = curIdx === stepIdx;
                return (
                  <div
                    key={step}
                    className={`ba-progress-step ${
                      isDone ? "done" : isCurrent ? "current" : ""
                    }`}
                  >
                    <span className="ba-progress-dot" />
                    <span className="ba-progress-label">{baStepCn(step)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}