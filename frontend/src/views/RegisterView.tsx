import { useEffect, useState, useCallback, useRef } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { RegEvent, RegAccount, RegStatus, PipelineConfig } from "../types";

// 渠道元信息 (name/label/hint) 由后端 /api/register/channels 动态返回,
// 前端不再硬编码固定列表 — 开源用户自建/启停渠道后下拉自动同步。
interface EmailChannel {
  name: string;
  label: string;
  hint: string;
}

function channelLabel(channels: EmailChannel[], c: string): string {
  const ch = channels.find((m) => m.name === c);
  if (ch) return ch.label;
  if (c.startsWith("imap:")) return `📧 ${c.slice(5)} (IMAP)`;
  if (c.startsWith("api798")) return "api798 卡密邮箱";
  return c;
}
function channelHint(channels: EmailChannel[], c: string): string {
  const ch = channels.find((m) => m.name === c);
  if (ch) return ch.hint;
  if (c.startsWith("imap:")) return "自建域名邮箱 · IMAP 取码 · 仅注册该域";
  return "自定义渠道";
}

const TYPE_CN: Record<string, string> = {
  start: "开始",
  log: "日志",
  progress: "进度",
  complete: "完成",
  error: "错误",
};

const STATUS_CN: Record<string, string> = {
  active: "存活",
  pending: "待验证",
  expired: "过期",
  suspended: "冻结",
  deactivated: "停用",
  logout: "登出",
  disabled: "失效",
  revoked: "吊销",
  unknown: "未知",
};

const STATUS_BADGE: Record<string, string> = {
  active: "badge-success",
  pending: "badge-warn",
  expired: "badge-warn",
  suspended: "badge-warn",
  deactivated: "badge-muted",
  logout: "badge-muted",
  disabled: "badge-danger",
  revoked: "badge-danger",
  unknown: "badge-muted",
};

const MODE_BADGE: Record<string, string> = {
  "163": "badge-accent",
};

const PLAN_BADGE: Record<string, string> = {
  plus: "badge-accent",
  pro: "badge-accent",
  team: "badge-warn",
  free: "badge-muted",
};

interface RegDetail extends RegAccount {
  password?: string | null;
  access_token?: string | null;
  session_token?: string | null;
  refresh_token?: string | null;
}

function maskSecret(s: string | null | undefined): string {
  if (!s) return "—";
  if (s.length <= 24) return s;
  return s.slice(0, 12) + "…" + s.slice(-8);
}

// 详情弹层: 可点击复制的字段行 (复制完整值, 不截断)
function DetailCopyRow({ label, value, k, copied, onCopy }: {
  label: string;
  value: string | null | undefined;
  k: string;
  copied: boolean;
  onCopy: (key: string, val: string | null | undefined) => void;
}) {
  const empty = !value;
  return (
    <div
      className={`detail-row${empty ? "" : " dr-copy"}`}
      title={empty ? undefined : "点击复制完整值"}
      onClick={empty ? undefined : () => onCopy(k, value)}
    >
      <span className="dr-label">{label}</span>
      <span className="dr-value mono dr-clip">
        {empty ? "—" : maskSecret(value)}
        {!empty && <span className={`dr-copy-badge${copied ? " show" : ""}`}>{copied ? "✓ 已复制" : "⧉"}</span>}
      </span>
    </div>
  );
}

export function RegisterView() {
  const pushLog = useStore((s) => s.pushLog);

  const [status, setStatus] = useState<RegStatus | null>(null);
  const [channels, setChannels] = useState<string[]>([]);
  const [channelMeta, setChannelMeta] = useState<EmailChannel[]>([]);
  const [count, setCount] = useState(1);
  const [emailMode, setEmailMode] = useState<string>("");
  const [cooldown, setCooldown] = useState(30);
  const [proxy, setProxy] = useState("");
  const [country, setCountry] = useState<string>("auto");
  const [countries, setCountries] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const [events, setEvents] = useState<RegEvent[]>([]);
  const [since, setSince] = useState(0);
  const [accounts, setAccounts] = useState<RegAccount[]>([]);
  const [stats, setStats] = useState<{ total: number; active: number; disabled: number } | null>(null);
  const [progress, setProgress] = useState<{ index: number; total: number; success: number; failed: number } | null>(null);
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [detail, setDetail] = useState<RegDetail | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [logLevel, setLogLevel] = useState("all");
  const logRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(true);

  const loadStatus = useCallback(async () => {
    try {
      const r = await api<RegStatus & { channels?: string[] }>("/api/register/status");
      if (r?.ok) {
        setStatus(r);
        if (Array.isArray(r.channels) && r.channels.length) {
          setChannels(r.channels);
          // 首次加载渠道后, 若 emailMode 仍为空则自动选第一个
          setEmailMode((prev) => prev && r.channels!.includes(prev) ? prev : (r.channels![0] || ""));
        }
        if (r.last_seq) setSince((prev) => Math.max(prev, r.last_seq));
      }
    } catch { /* ignore */ }
    // 拉取渠道元信息 (label/hint) — 与 status 分离, 启停渠道后下拉即时反映
    try {
      const c = await api<{ ok: boolean; channels: EmailChannel[] }>("/api/register/channels");
      if (c?.ok && Array.isArray(c.channels)) setChannelMeta(c.channels);
    } catch { /* ignore */ }
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set("search", search.trim());
      if (filterStatus) params.set("status", filterStatus);
      const r = await api<{ ok: boolean; items: RegAccount[] }>(
        "/api/register/accounts?" + params.toString()
      );
      if (r?.ok) setAccounts(r.items);
    } catch { /* ignore */ }
  }, [search, filterStatus]);

  const loadStats = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; total: number; active: number; disabled: number }>("/api/register/stats");
      if (r?.ok) setStats(r);
    } catch { /* ignore */ }
  }, []);

  const pollEvents = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const r = await api<{ ok: boolean; events: RegEvent[]; last_seq: number }>(
        "/api/register/events?since=" + since
      );
      if (r?.ok && r.events.length) {
        setEvents((prev) => [...prev, ...r.events].slice(-500));
        setSince(r.last_seq);
        for (const ev of r.events) {
          if (ev.type === "log" && ev.message) {
            pushLog(`[注册] ${ev.message}`, ev.stage === "engine" ? "warn" : "info");
          }
          if (ev.type === "progress" && ev.index !== undefined) {
            setProgress({ index: ev.index, total: ev.total ?? 0, success: ev.success ?? 0, failed: ev.failed ?? 0 });
          }
          if (ev.type === "complete") setProgress(null);
        }
      }
    } catch { /* ignore */ }
  }, [since, pushLog]);

  useEffect(() => {
    mountedRef.current = true;
    loadStatus();
    loadAccounts();
    loadStats();
    // 加载可选出口国家列表 (GEO 表 41 国)
    api<{ countries?: string[] }>("/api/register/countries").then((r) => {
      if (Array.isArray(r.countries) && r.countries.length) setCountries(r.countries);
    }).catch(() => { /* ignore */ });
    // 从一键流程配置回填注册参数 (复用 pipeline config 的 reg_* 字段, 跨会话记忆)
    api<{ config?: Partial<PipelineConfig> }>("/api/pipeline/config").then((r) => {
      const c = r?.config;
      if (!c) return;
      if (c.reg_email_mode) setEmailMode(c.reg_email_mode);
      if (typeof c.reg_cooldown === "number") setCooldown(c.reg_cooldown);
      if (typeof c.reg_proxy === "string") setProxy(c.reg_proxy);
      if (typeof c.reg_country === "string") setCountry(c.reg_country);
      // 回填完成后再放开回写 (延一拍, 避免上面的 setState 触发回写)
      setTimeout(() => { persistReady.current = true; }, 0);
    }).catch(() => {
      // 拉取失败也放开回写, 否则用户改了永远不落盘
      setTimeout(() => { persistReady.current = true; }, 0);
    });
    const t1 = setInterval(loadStatus, 3000);
    const t2 = setInterval(pollEvents, 3000);
    const t3 = setInterval(() => { loadAccounts(); loadStats(); }, 6000);
    return () => {
      mountedRef.current = false;
      clearInterval(t1); clearInterval(t2); clearInterval(t3);
    };
  }, [loadStatus, loadAccounts, loadStats, pollEvents]);

  // 注册参数变更 → 防抖回写 pipeline config (供下次打开记忆 + 一键流程共享)
  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const persistReady = useRef(false);  // 回填完成后才允许回写, 避免首渲染冲掉配置
  useEffect(() => {
    if (!persistReady.current) return;
    if (persistTimer.current) clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(() => {
      api("/api/pipeline/config", "POST", {
        reg_email_mode: emailMode,
        reg_cooldown: cooldown,
        reg_proxy: proxy,
        reg_country: country,
      }).catch(() => { /* ignore */ });
    }, 1200);
    return () => { if (persistTimer.current) clearTimeout(persistTimer.current); };
  }, [emailMode, cooldown, proxy, country]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [events]);

  // 详情弹层打开时支持 Esc 关闭
  useEffect(() => {
    if (!detail) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDetail(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [detail]);

  const handleStart = async () => {
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; error?: string }>("/api/register/start", "POST", {
        count: Number(count) || 1,
        email_mode: emailMode,
        cooldown: Number(cooldown) || 30,
        proxy: proxy.trim() || undefined,
        country: country || "auto",
      });
      if (r?.ok) {
        pushLog(`注册任务已启动: ${count} 个 (${channelLabel(channelMeta, emailMode)})`, "ok");
        setEvents([]);
        setSince(0);
        setProgress(null);
        await loadStatus();
      } else {
        pushLog(`启动失败: ${r?.error || "未知原因"}`, "err");
      }
    } catch (e) {
      pushLog("启动失败: " + (e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    try {
      const r = await api<{ ok: boolean; stopped: boolean }>("/api/register/stop", "POST");
      pushLog(r?.stopped ? "已请求停止（当前号跑完后停止）" : "当前无运行中任务", r?.stopped ? "warn" : "info");
    } catch (e) {
      pushLog("停止失败: " + (e as Error).message, "err");
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("确认删除该注册账号记录？")) return;
    try {
      const r = await api(`/api/register/accounts/${id}`, "DELETE");
      if (r?.ok) {
        pushLog(`已删除账号 #${id}`, "ok");
        loadAccounts();
        loadStats();
      }
    } catch (e) {
      pushLog("删除失败: " + (e as Error).message, "err");
    }
  };

  const handleDetail = async (id: number) => {
    try {
      const r = await api<{ ok: boolean; account: RegDetail }>(`/api/register/accounts/${id}`);
      if (r?.ok) setDetail(r.account);
    } catch { /* ignore */ }
  };

  const [copiedKey, setCopiedKey] = useState<string>("");
  const handleCopyField = async (key: string, value: string | null | undefined) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(""), 1500);
    } catch { /* ignore */ }
  };

  // 批量管理
  const allSelected = accounts.length > 0 && accounts.every((a) => selected.has(a.id));
  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    setSelected(allSelected ? new Set() : new Set(accounts.map((a) => a.id)));
  };
  const handleBulkDelete = async () => {
    if (selected.size === 0) return;
    if (!window.confirm(`确认删除选中的 ${selected.size} 个账号？此操作不可撤销。`)) return;
    try {
      const r = await api<{ ok: boolean; deleted?: number; error?: string }>("/api/register/accounts/bulk_delete", "POST", {
        account_ids: [...selected],
      });
      if (r?.ok) {
        pushLog(`批量删除 ${r.deleted || 0} 个账号`, "ok");
        setSelected(new Set());
        loadAccounts();
        loadStats();
      } else {
        pushLog(`批量删除失败: ${r?.error || "未知"}`, "err");
      }
    } catch (e) {
      pushLog("批量删除失败: " + (e as Error).message, "err");
    }
  };
  const handleBulkExport = () => {
    const picked = accounts.filter((a) => selected.has(a.id));
    if (picked.length === 0) return;
    const header = "ID,邮箱,渠道,套餐,状态,注册时间\n";
    const rows = picked.map((a) =>
      [a.id, a.email, a.email_mode || "", a.plan_type || "", a.alive_status || "", a.register_ts || a.created_at || ""]
        .map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")
    ).join("\n");
    const blob = new Blob(["\ufeff" + header + rows], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `register_accounts_${selected.size}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const successRate = stats && stats.total > 0 ? ((stats.active / stats.total) * 100).toFixed(0) : "—";
  const visibleEvents = events.filter((ev) => {
    if (logLevel === "all") return true;
    if (logLevel === "err") return ev.type === "error" || (ev.type === "log" && /(失败|错误|error|fail|✗)/i.test(ev.message || ""));
    if (logLevel === "ok") return ev.type === "complete" || ev.type === "progress" || (ev.type === "log" && /(成功|✓|OK|ok=)/i.test(ev.message || ""));
    return true;
  });

  return (
    <div className="page page-wide">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <h2 className="page-title">账号注册</h2>
        <span className={`badge ${status?.running ? "badge-info" : "badge-muted"}`}>
          {status?.running ? "● 任务运行中" : "○ 空闲"}
        </span>
      </div>

      {/* 统计卡 */}
      <div className="stat-grid" style={{ marginBottom: 14 }}>
        <div className="stat-card">
          <div className="stat-label">累计注册</div>
          <div className="stat-value">{stats?.total ?? "—"}</div>
          <div className="stat-foot">全部渠道</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">存活</div>
          <div className="stat-value" style={{ color: "var(--ok)" }}>{stats?.active ?? "—"}</div>
          <div className="stat-foot">alive</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">失效</div>
          <div className="stat-value" style={{ color: "var(--danger)" }}>{stats?.disabled ?? "—"}</div>
          <div className="stat-foot">disabled</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">存活率</div>
          <div className="stat-value">{successRate}%</div>
          <div className="stat-foot">active / total</div>
        </div>
      </div>

      {/* 任务控制 */}
      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">批量注册</span>
          {progress && (
            <span className="running-chip" style={{ marginLeft: 8 }}>
              第 {progress.index}/{progress.total} 号 · 成功 {progress.success} · 失败 {progress.failed}
            </span>
          )}
        </div>
        <div className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", marginTop: 12 }}>
          <label className="field">
            <span className="field-label">注册数量</span>
            <input className="input" type="number" min={1} max={200} value={count}
              onChange={(e) => setCount(Math.min(Math.max(Number(e.target.value) || 1, 1), 200))} />
          </label>
          <label className="field">
            <span className="field-label">邮箱渠道</span>
            <select className="select" value={emailMode} onChange={(e) => setEmailMode(e.target.value)}>
              {channels.map((c) => (
                <option key={c} value={c}>{channelLabel(channelMeta, c)}</option>
              ))}
            </select>
            <span className="field-hint">{channelHint(channelMeta, emailMode)}</span>
          </label>
          <label className="field">
            <span className="field-label">号间冷却 (秒)</span>
            <input className="input" type="number" min={0} max={600} value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value) || 0)} />
          </label>
          <label className="field">
            <span className="field-label">出口国家</span>
            <select className="select" value={country}
              onChange={(e) => setCountry(e.target.value)}
              title="注册出口 IP 国家。auto=随机; 指定国家则用 711 按该国构造住宅代理">
              <option value="auto">auto (随机)</option>
              {countries.map((cc) => (
                <option key={cc} value={cc}>{cc}</option>
              ))}
            </select>
            <span className="field-hint">
              {country && country !== "auto" ? `按 ${country} 出口注册` : "随机选国家"}
            </span>
          </label>
          <label className="field">
            <span className="field-label">代理出口</span>
            <input className="input" type="text" placeholder="留空 = 自动 711 中继"
              value={proxy} onChange={(e) => setProxy(e.target.value)} />
            <span className="field-hint">http://user:pass@host:port 或 711 地址</span>
          </label>
        </div>
        {progress && (
          <div className="progress" style={{ marginTop: 14 }}>
            <div className="progress-bar" style={{ width: `${(progress.index / progress.total) * 100}%` }} />
          </div>
        )}
        <div className="btn-row" style={{ marginTop: 14 }}>
          <button className="btn btn-primary" disabled={busy || !!status?.running} onClick={handleStart}>
            {busy ? "启动中…" : "启动注册"}
          </button>
          <button className="btn btn-stop-live" disabled={!status?.running} onClick={handleStop}>
            停止任务
          </button>
          <span className="muted" style={{ alignSelf: "center" }}>
            成功账号自动进入 Token 库（source=register），可直接用于提链
          </span>
        </div>
      </section>

      {/* 实时日志 */}
      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">实时日志</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <select className="select" style={{ width: 130 }} value={logLevel} onChange={(e) => setLogLevel(e.target.value)}>
              <option value="all">全部</option>
              <option value="ok">成功</option>
              <option value="err">失败/错误</option>
            </select>
            <button className="btn btn-sm" onClick={() => setEvents([])}>清空</button>
          </div>
        </div>
        <div className="log-panel" style={{ marginTop: 8 }}>
          <div className="log-body" ref={logRef} style={{ maxHeight: 300 }}>
            {visibleEvents.length === 0 && (
              <div className="empty" style={{ padding: "28px 0" }}>
                <div className="empty-title">暂无日志</div>
                <div className="empty-hint">启动任务后实时刷新</div>
              </div>
            )}
            {visibleEvents.map((ev) => {
              const cls =
                ev.type === "error" ? "err" :
                ev.type === "complete" ? "ok" :
                ev.type === "log" ? (/fail|失败|error/i.test(ev.message || "") ? "warn" : "info") :
                "info";
              return (
                <div key={ev.seq} className={`log-line ${cls}`}>
                  <span className="log-ts">{ev.ts?.slice(11, 19)}</span>
                  <span className="log-chain">{TYPE_CN[ev.type] || ev.type}</span>
                  <span className="log-msg">
                    {ev.message || (ev.type === "progress" ? `第 ${ev.index}/${ev.total} 号完成` : "")}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* 账号表格 */}
      <section className="card">
        <div className="card-head">
          <span className="card-title">注册账号</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select className="select flex-field-sm" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
              <option value="">全部状态</option>
              <option value="active">存活</option>
              <option value="disabled">失效</option>
            </select>
            <input className="input flex-field" placeholder="搜索邮箱…" value={search}
              onChange={(e) => setSearch(e.target.value)} />
            <button className="btn btn-sm" onClick={() => { loadAccounts(); loadStats(); }}>刷新</button>
          </div>
        </div>
        <div className="table-wrap" style={{ marginTop: 8 }}>
          {selected.size > 0 && (
            <div className="batch-bar">
              <span className="tag">已选 {selected.size}</span>
              <button className="btn btn-sm btn-danger" onClick={handleBulkDelete}>删除所选</button>
              <button className="btn btn-sm" onClick={handleBulkExport}>导出 CSV</button>
              <button className="btn btn-sm btn-ghost" onClick={() => setSelected(new Set())}>取消选择</button>
            </div>
          )}
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
                </th>
                <th className="num">ID</th>
                <th>邮箱</th>
                <th>渠道</th>
                <th>套餐</th>
                <th>状态</th>
                <th>错误码</th>
                <th>注册时间</th>
                <th>Token</th>
                <th className="num">操作</th>
              </tr>
            </thead>
            <tbody>
              {accounts.length === 0 && (
                <tr>
                  <td colSpan={10}>
                    <div className="empty" style={{ padding: "24px 0" }}>
                      <div className="empty-title">暂无注册记录</div>
                      <div className="empty-hint">启动注册任务后，结果将在此展示</div>
                    </div>
                  </td>
                </tr>
              )}
              {accounts.map((a) => (
                <tr key={a.id} className={selected.has(a.id) ? "row-selected" : ""}>
                  <td>
                    <input type="checkbox" checked={selected.has(a.id)} onChange={() => toggleSelect(a.id)} />
                  </td>
                  <td className="num mono">{a.id}</td>
                  <td className="mono">{a.email}</td>
                  <td>
                    <span className={`badge ${MODE_BADGE[a.email_mode || ""] || "badge-muted"}`}>
                      {a.email_mode || "—"}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${PLAN_BADGE[a.plan_type || ""] || "badge-muted"}`}>
                      {a.plan_type || "—"}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[a.alive_status] || "badge-muted"}`}>
                      {STATUS_CN[a.alive_status] || a.alive_status}
                    </span>
                  </td>
                  <td className="mono">{a.error_code || "—"}</td>
                  <td className="mono">{a.register_ts?.slice(0, 19) || a.created_at?.slice(0, 19) || "—"}</td>
                  <td>
                    {a.has_access_token && <span className="badge badge-info">at</span>}
                    {a.has_session_token && <span className="badge badge-info" style={{ marginLeft: 4 }}>st</span>}
                    {!a.has_access_token && <span className="badge badge-muted">无</span>}
                  </td>
                  <td className="num">
                    <button className="btn btn-sm" onClick={() => handleDetail(a.id)}>详情</button>
                    <button className="btn btn-sm btn-danger" style={{ marginLeft: 4 }} onClick={() => handleDelete(a.id)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* 详情弹层 */}
      {detail && (
        <div className="overlay" onClick={() => setDetail(null)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <span className="sheet-title">账号详情 #{detail.id}</span>
              <span className="muted" style={{ fontSize: 11, fontWeight: 400 }}>点击任意字段值可复制完整内容</span>
            </div>
            <div className="sheet-body">
              <div className="detail-list">
                <DetailCopyRow label="邮箱" value={detail.email} k="email" copied={copiedKey === "email"} onCopy={handleCopyField} />
                <DetailCopyRow label="密码" value={detail.password} k="password" copied={copiedKey === "password"} onCopy={handleCopyField} />
                <DetailCopyRow label="AccessToken" value={detail.access_token} k="access_token" copied={copiedKey === "access_token"} onCopy={handleCopyField} />
                <DetailCopyRow label="SessionToken" value={detail.session_token} k="session_token" copied={copiedKey === "session_token"} onCopy={handleCopyField} />
                <DetailCopyRow label="RefreshToken" value={detail.refresh_token} k="refresh_token" copied={copiedKey === "refresh_token"} onCopy={handleCopyField} />
                <div className="detail-row">
                  <span className="dr-label">套餐</span>
                  <span className="dr-value">{detail.plan_type || "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">状态</span>
                  <span className="dr-value">
                    <span className={`badge ${STATUS_BADGE[detail.alive_status] || "badge-muted"}`}>
                      {STATUS_CN[detail.alive_status] || detail.alive_status}
                    </span>
                    {" "}
                    <span className={`badge ${detail.status === "active" ? "badge-success" : "badge-danger"}`}>
                      {detail.status}
                    </span>
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">渠道</span>
                  <span className="dr-value">{detail.email_mode || "—"}</span>
                </div>
                <DetailCopyRow label="来源邮箱" value={detail.source_email} k="source_email" copied={copiedKey === "source_email"} onCopy={handleCopyField} />
                <div className="detail-row">
                  <span className="dr-label">注册时间</span>
                  <span className="dr-value mono">{detail.register_ts || detail.created_at || "—"}</span>
                </div>
                {detail.error_detail && (
                  <div className="detail-row">
                    <span className="dr-label">失败原因</span>
                    <span className="dr-error" style={{ wordBreak: "break-all" }}>{detail.error_detail}</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}