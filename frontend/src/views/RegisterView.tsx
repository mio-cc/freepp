import { useEffect, useState, useCallback, useRef } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { RegEvent, RegAccount } from "../types";

const EMAIL_MODES = [
  { value: "mailtm", label: "Mail.tm 临时邮箱", hint: "零依赖在线 API，直连取号" },
  { value: "163", label: "163 IMAP 邮箱", hint: "需 REG_IMAP_ACCOUNTS 环境变量注入" },
] as const;

const TYPE_CN: Record<string, string> = {
  start: "任务开始",
  log: "日志",
  progress: "进度",
  complete: "任务结束",
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

export function RegisterView() {
  const pushLog = useStore((s) => s.pushLog);

  const [running, setRunning] = useState(false);
  const [count, setCount] = useState(1);
  const [emailMode, setEmailMode] = useState<string>("mailtm");
  const [cooldown, setCooldown] = useState(30);
  const [proxy, setProxy] = useState("");
  const [busy, setBusy] = useState(false);

  const [events, setEvents] = useState<RegEvent[]>([]);
  const [since, setSince] = useState(0);
  const [accounts, setAccounts] = useState<RegAccount[]>([]);
  const [stats, setStats] = useState<{ total: number; active: number; disabled: number } | null>(null);
  const [progress, setProgress] = useState<{ index: number; total: number; success: number; failed: number } | null>(null);
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<RegAccount & { password?: string; access_token?: string; session_token?: string } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const loadStatus = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; running: boolean; last_seq: number }>("/api/register/status");
      if (r) {
        setRunning(!!r.running);
        if (r.last_seq) setSince((prev) => Math.max(prev, r.last_seq));
      }
    } catch { /* ignore */ }
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; items: RegAccount[] }>(
        "/api/register/accounts?search=" + encodeURIComponent(search)
      );
      if (r?.ok) setAccounts(r.items);
    } catch { /* ignore */ }
  }, [search]);

  const loadStats = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; total: number; active: number; disabled: number }>("/api/register/stats");
      if (r?.ok) setStats(r);
    } catch { /* ignore */ }
  }, []);

  const pollEvents = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; events: RegEvent[]; last_seq: number }>(
        "/api/register/events?since=" + since
      );
      if (r?.ok && r.events.length) {
        setEvents((prev) => [...prev, ...r.events].slice(-400));
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
    loadStatus();
    loadAccounts();
    loadStats();
    const t1 = setInterval(loadStatus, 3000);
    const t2 = setInterval(pollEvents, 3000);
    const t3 = setInterval(() => { loadAccounts(); loadStats(); }, 5000);
    return () => { clearInterval(t1); clearInterval(t2); clearInterval(t3); };
  }, [loadStatus, loadAccounts, loadStats, pollEvents]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [events]);

  const handleStart = async () => {
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; error?: string }>("/api/register/start", "POST", {
        count: Number(count) || 1,
        email_mode: emailMode,
        cooldown: Number(cooldown) || 30,
        proxy: proxy.trim() || undefined,
      });
      if (r?.ok) {
        pushLog(`注册任务已启动: ${count} 个 (${emailMode})`, "ok");
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
      const r = await api<{ ok: boolean; account: RegAccount & { password?: string; access_token?: string; session_token?: string } }>(
        `/api/register/accounts/${id}`
      );
      if (r?.ok) setDetail(r.account);
    } catch { /* ignore */ }
  };

  const successRate = stats && stats.total > 0 ? ((stats.active / stats.total) * 100).toFixed(0) : "—";

  return (
    <div className="view">
      <h1 className="view-title">GPT 账号注册</h1>
      <p className="view-sub">
        注册协议复刻自 mail-otp-server / codex_register：next-auth OAuth → OTP → sentinel create_account → token。
        成功账号自动写入 Token 库（source=register），可直接用于提链。
      </p>

      {/* 控制卡片 */}
      <section className="card" style={{ marginBottom: 12 }}>
        <div className="card-head">
          <span className="card-title">批量注册</span>
          {running && <span className="running-chip">任务运行中</span>}
          {progress && (
            <span className="running-chip" style={{ marginLeft: 8 }}>
              第 {progress.index}/{progress.total} 号 · 成功 {progress.success} · 失败 {progress.failed}
            </span>
          )}
        </div>
        <div className="form-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, marginTop: 12 }}>
          <label className="field">
            <span className="field-label">注册数量</span>
            <input className="input" type="number" min={1} max={200} value={count} onChange={(e) => setCount(Number(e.target.value))} />
          </label>
          <label className="field">
            <span className="field-label">邮箱渠道</span>
            <select className="input" value={emailMode} onChange={(e) => setEmailMode(e.target.value)}>
              {EMAIL_MODES.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
            <span className="field-hint">{EMAIL_MODES.find((m) => m.value === emailMode)?.hint}</span>
          </label>
          <label className="field">
            <span className="field-label">号间冷却 (秒)</span>
            <input className="input" type="number" min={0} value={cooldown} onChange={(e) => setCooldown(Number(e.target.value))} />
          </label>
          <label className="field">
            <span className="field-label">代理 (留空自动 711)</span>
            <input className="input" type="text" placeholder="http://user:pass@host:port" value={proxy} onChange={(e) => setProxy(e.target.value)} />
          </label>
        </div>
        <div className="card-actions" style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button className="btn btn-primary" disabled={busy || running} onClick={handleStart}>
            {busy ? "启动中..." : "启动注册"}
          </button>
          <button className="btn" disabled={!running} onClick={handleStop}>停止任务</button>
          {stats && (
            <span className="stat-inline" style={{ marginLeft: "auto", alignSelf: "center" }}>
              累计 <b>{stats.total}</b> · 存活 <b className="ok-text">{stats.active}</b> · 失效 <b className="err-text">{stats.disabled}</b> · 存活率 <b>{successRate}%</b>
            </span>
          )}
        </div>
      </section>

      {/* 日志 */}
      <section className="card" style={{ marginBottom: 12 }}>
        <div className="card-head">
          <span className="card-title">实时日志</span>
          <button className="btn btn-sm" onClick={() => setEvents([])}>清空</button>
        </div>
        <div className="log-box mono" ref={logRef} style={{ height: 220, overflowY: "auto", marginTop: 8 }}>
          {events.length === 0 && <div className="muted">暂无日志，启动任务后实时刷新...</div>}
          {events.map((ev) => (
            <div key={ev.seq} className={`log-line ${ev.type === "error" ? "err" : ev.type === "complete" ? "ok" : ""}`}>
              <span className="log-ts">{ev.ts?.slice(11, 19)}</span>
              <span className="log-tag">{TYPE_CN[ev.type] || ev.type}</span>
              <span className="log-msg">{ev.message || (ev.type === "progress" ? `第 ${ev.index}/${ev.total} 号完成` : "")}</span>
            </div>
          ))}
        </div>
      </section>

      {/* 账号表格 */}
      <section className="card">
        <div className="card-head">
          <span className="card-title">注册账号</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input className="input" placeholder="搜索邮箱..." value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 200 }} />
            <button className="btn btn-sm" onClick={() => { loadAccounts(); loadStats(); }}>刷新</button>
          </div>
        </div>
        <table className="table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>ID</th>
              <th>邮箱</th>
              <th>渠道</th>
              <th>套餐</th>
              <th>状态</th>
              <th>错误码</th>
              <th>注册时间</th>
              <th>凭据</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {accounts.length === 0 && (
              <tr><td colSpan={9} className="muted center">暂无注册记录</td></tr>
            )}
            {accounts.map((a) => (
              <tr key={a.id}>
                <td className="mono">{a.id}</td>
                <td className="mono">{a.email}</td>
                <td>{a.email_mode || "—"}</td>
                <td>{a.plan_type || "—"}</td>
                <td>
                  <span className={`badge ${a.status === "active" ? "badge-success" : "badge-danger"}`}>
                    {STATUS_CN[a.alive_status] || a.alive_status}
                  </span>
                </td>
                <td className="mono">{a.error_code || "—"}</td>
                <td className="mono">{a.register_ts?.slice(0, 19) || a.created_at?.slice(0, 19) || "—"}</td>
                <td>
                  <span className="badge badge-info">{a.has_access_token ? "token" : "—"}</span>
                  {a.has_session_token && <span className="badge badge-info" style={{ marginLeft: 4 }}>session</span>}
                </td>
                <td>
                  <button className="btn btn-sm" onClick={() => handleDetail(a.id)}>详情</button>
                  <button className="btn btn-sm btn-danger" onClick={() => handleDelete(a.id)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* 详情抽屉 */}
      {detail && (
        <div className="modal-overlay" onClick={() => setDetail(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span className="modal-title">账号详情 #{detail.id}</span>
              <button className="btn btn-sm" onClick={() => setDetail(null)}>关闭</button>
            </div>
            <div className="modal-body" style={{ display: "grid", gap: 8 }}>
              <div className="kv"><span className="kv-k">邮箱</span><span className="kv-v mono">{detail.email}</span></div>
              <div className="kv"><span className="kv-k">密码</span><span className="kv-v mono">{detail.password || "—"}</span></div>
              <div className="kv"><span className="kv-k">AccessToken</span><span className="kv-v mono wrap">{detail.access_token || "—"}</span></div>
              <div className="kv"><span className="kv-k">SessionToken</span><span className="kv-v mono wrap">{detail.session_token || "—"}</span></div>
              <div className="kv"><span className="kv-k">套餐</span><span className="kv-v">{detail.plan_type || "—"}</span></div>
              <div className="kv"><span className="kv-k">状态</span><span className="kv-v">{detail.alive_status} / {detail.status}</span></div>
              <div className="kv"><span className="kv-k">错误</span><span className="kv-v mono wrap">{detail.error_detail || detail.error_code || "—"}</span></div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}