import { useState, useEffect, useCallback, useRef } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { BAAuthRecord, BAAuthConfig, BAStep, SMAQuote, BAFeedItem, BABaSnap } from "../types";
import { BA_STEPS, BA_STEP_CN } from "../types";

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

/* 接码预算上限阶梯预设 (USD/号) */
const SMS_PRICE_TIERS = ["0.01", "0.02", "0.05", "0.10", "0.25", "0.50"] as const;

export function PayPalView() {
  const pushLog = useStore((s) => s.pushLog);
  const chainStates = useStore((s) => s.chainStates);

  const [baRecords, setBaRecords] = useState<BAAuthRecord[]>([]);
  const [config, setConfig] = useState<BAAuthConfig>({
    sms_provider: "smsbower",
    sms_api_key: "",
    sms_price: "0.05",
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
  const [now, setNow] = useState(Date.now());
  const baFeedRef = useRef<ReturnType<typeof useStore.getState>["baFeed"]>(baFeed);
  baFeedRef.current = baFeed;

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
  useEffect(() => {
    if (saveConfigTimer.current) clearTimeout(saveConfigTimer.current);
    saveConfigTimer.current = setTimeout(async () => {
      try {
        await api("/api/paypal/ba/config", "POST", config);
      } catch {
        /* 后端不可用时忽略 */
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
          const snap: BABaSnap = { status: r.status, step: r.step, error: r.error, source: r.source || "", last_msg: r.last_msg || "" };
          next.set(r.ba_token, snap);
          const p = prev?.get(r.ba_token);
          if (!p) {
            items.push({
              ts: Date.now(), token: r.ba_token, level: "info",
              msg: `${r.source === "manual" ? "手动导入" : "加入队列"} · 国家 ${r.country || "?"}`,
            });
            continue;
          }
          if (p.status !== r.status) {
            if (r.status === "running") {
              items.push({ ts: Date.now(), token: r.ba_token, level: "info", msg: `授权启动 · 步骤 ${BA_STEP_CN[r.step]}` });
            } else if (r.status === "success") {
              items.push({ ts: Date.now(), token: r.ba_token, level: "ok", msg: "授权成功 ✓" });
            } else if (r.status === "failed") {
              items.push({ ts: Date.now(), token: r.ba_token, level: "err", msg: `授权失败: ${r.error || "未知原因"}` });
            } else if (r.status === "pending") {
              items.push({ ts: Date.now(), token: r.ba_token, level: "warn", msg: "重新入队 (重试)" });
            }
          } else if (r.status === "running" && p.step !== r.step) {
            items.push({ ts: Date.now(), token: r.ba_token, level: "info", msg: `步骤 → ${BA_STEP_CN[r.step]}${r.last_msg ? ` · ${r.last_msg}` : ""}` });
          } else if (r.status === "running" && r.last_msg && p.last_msg !== r.last_msg) {
            items.push({ ts: Date.now(), token: r.ba_token, level: "info", msg: r.last_msg });
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

  // running 记录已运行时长: 每秒刷新一次
  useEffect(() => {
    if (!baRecords.some((r) => r.status === "running")) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [baRecords]);

  const loadQuote = useCallback(
    async (cc: string) => {
      if (!cc) return;
      setQuoteLoading(cc);
      try {
        const res = await api(`/api/paypal/sms/quote?country=${cc}`, "GET");
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
    []
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

  // 批量重试失败记录
  const handleRetryTokens = async (tokens: string[]) => {
    const targets = baRecords.filter((r) => tokens.includes(r.ba_token) && r.status === "failed");
    if (targets.length === 0) {
      pushLog("所选记录中没有 failed 状态", "warn", "paypal");
      return;
    }
    const groupText = [...new Set(targets.map((r) => (r.identity_country || r.country || "BR").toUpperCase()))].join(" / ");
    if (!window.confirm(`重试 ${targets.length} 条失败 BA (${groupText}, 并发上限 ${config.max_concurrent ?? 3})?`)) return;
    pushLog(`批量重试: ${targets.length} 条 BA`, "info", "paypal");
    try {
      const res = await api("/api/paypal/ba/retry", "POST", {
        ba_tokens: targets.map((r) => r.ba_token),
        config,
      });
      if (res && res.ok) {
        pushLog(`重试已启动: ${res.started}/${res.total} 条`, "ok", "paypal");
        if (res.skipped && Object.keys(res.skipped).length > 0) {
          pushLog(`重试跳过: ${JSON.stringify(res.skipped)}`, "warn", "paypal");
        }
        fetchBaRecords();
        setSelected(new Set());
      }
    } catch {
      pushLog("批量重试失败 (后端不可用)", "warn", "paypal");
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

  // 接码报价统计 (预算行提示: 该国最低价 + 预算内可用家数)
  const smsQuoteCc = (config.sms_country || config.identity_country || "BR").toUpperCase();
  const smsQuotes = quotes[smsQuoteCc];
  const smsMinPrice =
    smsQuotes && smsQuotes.length > 0
      ? Math.min(...smsQuotes.map((q) => q.price))
      : null;
  const smsBudget = parseFloat(config.sms_price);
  const smsInBudget =
    smsQuotes && smsQuotes.length > 0
      ? smsQuotes.filter((q) => (smsBudget > 0 ? q.price <= smsBudget : true)).length
      : 0;

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
    <div className="page">
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
          <span className="tag">运行中 {stats.running}</span>
          <span className="tag" style={{ color: "var(--ok)" }}>成功 {stats.success}</span>
          <span className="tag" style={{ color: "var(--danger)" }}>失败 {stats.failed}</span>
          <button className="btn btn-sm btn-ghost" onClick={() => clearBaFeed()}>
            清空日志
          </button>
        </div>
        <div className="card-body">
          {runningList.length > 0 && (
            <div className="running-strip">
              {runningList.map((r) => (
                <div className="running-chip" key={r.ba_token} title={`${r.ba_token} · ${r.identity_country || r.country || "?"}`}>
                  <span className="spinner" />
                  <code className="mono">{r.ba_token.slice(0, 14)}…</code>
                  <span>{BA_STEP_CN[r.step]}</span>
                  <span className="tag">{r.identity_country || r.country || "?"}</span>
                  {r.last_msg && (
                    <span className="feed-msg" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.last_msg}
                    </span>
                  )}
                  <span className="feed-ts">
                    {Math.max(0, Math.floor((now - (r.updated_at || now)) / 1000))}s
                  </span>
                </div>
              ))}
            </div>
          )}
          {baFeed.length === 0 ? (
            <div className="feed-empty">
              暂无授权日志 — 队列有状态变化时 (导入/启动/步骤/取号/OTP/成功/失败/删除) 实时显示在此
            </div>
          ) : (
            <div className="monitor-feed">
              {baFeed.map((f, i) => (
                <div className="feed-row" key={`${f.ts}-${i}`}>
                  <span className="feed-ts">
                    {new Date(f.ts).toLocaleTimeString("zh-CN", { hour12: false })}
                  </span>
                  <span className={`badge ${FEED_BADGE[f.level]}`}>{FEED_LABEL[f.level]}</span>
                  <code className="feed-token">{f.token.slice(0, 12)}…</code>
                  <span className="feed-msg">{f.msg}</span>
                </div>
              ))}
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
                disabled={selectedList.every((r) => r.status !== "failed")}
              >
                重试所选
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
                        <span className="tag">{BA_STEP_CN[r.step]}</span>
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
                        {r.status === "failed" && (
                          <button
                            className="btn btn-sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRetryTokens([r.ba_token]);
                            }}
                          >
                            重试
                          </button>
                        )}
                        {r.status === "running" && <span className="spinner" />}
                        {r.status === "success" && (
                          <>
                            <span style={{ color: "var(--ok)" }}>✓</span>
                            <button
                              className="btn btn-sm"
                              title="重跑将消耗新号/新卡, 用于订阅未生效等场景"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (!window.confirm(`重跑已授权 ${r.ba_token}?\n将重新走完整授权流程 (消耗新接码号/新卡)。确认?`)) return;
                                api("/api/paypal/ba/retry", "POST", {
                                  ba_tokens: [r.ba_token],
                                  config: { ...buildRecordConfig(r), allow_success_retry: true },
                                }).then((res) => {
                                  if (res && res.ok) {
                                    pushLog(`已授权重跑已启动: ${r.ba_token}`, "ok", "paypal");
                                    fetchBaRecords();
                                  } else {
                                    pushLog(`重跑失败: ${res?.error || "未知"}`, "warn", "paypal");
                                  }
                                });
                              }}
                            >
                              重跑
                            </button>
                          </>
                        )}
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
          </div>
          <div className="card-body">
            <div className="setting-row">
              <span className="setting-label">接码平台</span>
              <div className="setting-control">
                <select
                  className="select"
                  value={config.sms_provider}
                  onChange={(e) =>
                    setConfig({ ...config, sms_provider: e.target.value })
                  }
                >
                  <option value="smsbower">SMSBower (已接入)</option>
                  <option value="sms_activate" disabled>SMS-Activate (未接入)</option>
                  <option value="5sim" disabled>5SIM (未接入)</option>
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">接码平台 API Key</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="password"
                  value={config.sms_api_key || ""}
                  placeholder="留空使用 backend/ba_paypal/.env 中的 key"
                  onChange={(e) =>
                    setConfig({ ...config, sms_api_key: e.target.value })
                  }
                  style={{ width: 260 }}
                />
                <span className="setting-hint">保存在前端 config, 授权时覆盖 .env (仅本次会话)</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">接码预算上限 (USD/号)</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={0.005}
                  value={config.sms_price}
                  title="阶梯取号: 只尝试价格 ≤ 上限的供应商 (从最低价开始), 0 = 不限"
                  onChange={(e) =>
                    setConfig({ ...config, sms_price: e.target.value })
                  }
                  style={{ width: 84 }}
                />
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}>
                  {SMS_PRICE_TIERS.map((t) => (
                    <button
                      key={t}
                      className={`btn btn-sm ${config.sms_price === t ? "btn-primary" : ""}`}
                      onClick={() => setConfig({ ...config, sms_price: t })}
                    >
                      ${t}
                    </button>
                  ))}
                  <button
                    className={`btn btn-sm ${Number(config.sms_price) <= 0 ? "btn-primary" : ""}`}
                    onClick={() => setConfig({ ...config, sms_price: "0" })}
                  >
                    不限
                  </button>
                </div>
                <span className="setting-hint">
                  {smsMinPrice != null
                    ? `阶梯取号: 最低 $${smsMinPrice.toFixed(4)} · ≤上限 ${smsInBudget} 家可用 (${smsQuoteCc})`
                    : `阶梯取号: 报价查询中 (${smsQuoteCc})…`}
                </span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">换号超时 (秒)</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  value={config.sms_timeout}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      sms_timeout: parseInt(e.target.value) || 15,
                    })
                  }
                />
              </div>
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
                <span className="setting-hint">默认跟随出口国家</span>
              </div>
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
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">最大重试 (卡/流程)</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  value={config.max_retries}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      max_retries: parseInt(e.target.value) || 3,
                    })
                  }
                />
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
                <div className="detail-row">
                  <span className="dr-label">BA Token</span>
                  <span className="dr-value">{detailRecord.ba_token}</span>
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
                  <span className="dr-value">{BA_STEP_CN[detailRecord.step]}</span>
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
                    <span className="ba-progress-label">{BA_STEP_CN[step]}</span>
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