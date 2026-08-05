import { useState, useEffect, useCallback } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { BAAuthRecord, BAAuthConfig, BAStep } from "../types";
import { BA_STEPS, BA_STEP_CN } from "../types";

/* ── 授权流程步骤定义 ── */
const STEP_FLOW: { step: BAStep; icon: string; desc: string }[] = [
  { step: "submit_email", icon: "1", desc: "提交 PayPal 邮箱，进入登录流程" },
  { step: "captcha", icon: "2", desc: "触发验证码 (hCaptcha passive / reCAPTCHA Enterprise)" },
  { step: "sms", icon: "3", desc: "短信验证码验证 (SMSBower 接码)" },
  { step: "signup", icon: "4", desc: "注册 PayPal 新会员 (SignUp)" },
  { step: "consent_ba", icon: "5", desc: "同意 Billing Agreement 授权" },
  { step: "done", icon: "6", desc: "获取 EUAT，BA 授权完成" },
];

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
    sms_price: "0.008",
    sms_timeout: 15,
    exit_country: "BR",
    proxy_type: "711_sticky",
    captcha_strategy: "dense_signal_reorder_v1",
    max_retries: 3,
  });
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [detailRecord, setDetailRecord] = useState<BAAuthRecord | null>(null);

  const pendingFromChains = Object.values(chainStates).filter(
    (c) => c.status === "success" && c.url && c.url.includes("ba_token=BA-")
  );

  const fetchBaRecords = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api("/api/paypal/ba/records", "GET");
      if (res && res.records) {
        setBaRecords(res.records);
      }
    } catch {
      const mockRecords: BAAuthRecord[] = pendingFromChains.map((c) => {
        const baMatch = c.url?.match(/ba_token=(BA-[A-Za-z0-9]+)/);
        return {
          ba_token: baMatch?.[1] || "",
          email: c.email,
          approve_url: c.url || "",
          status: "pending" as const,
          step: "submit_email" as BAStep,
          country: c.country,
          chain_id: Object.keys(chainStates).find(
            (k) => chainStates[k] === c
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
  }, [chainStates, pendingFromChains]);

  useEffect(() => {
    fetchBaRecords();
  }, [fetchBaRecords]);

  const handleStartAuth = async (baToken: string) => {
    if (!baToken) return;
    pushLog(`BA 授权启动: ${baToken}`, "info", "paypal");
    try {
      const res = await api("/api/paypal/ba/authorize", "POST", {
        ba_token: baToken,
        config,
      });
      if (res && res.ok) {
        pushLog(`BA 授权已启动: ${baToken}`, "ok", "paypal");
        fetchBaRecords();
      }
    } catch {
      pushLog(`BA 授权启动失败 (后端不可用): ${baToken}`, "warn", "paypal");
    }
  };

  const handleBatchAuth = async () => {
    const pending = baRecords.filter((r) => r.status === "pending");
    if (pending.length === 0) {
      pushLog("没有待授权的 BA 记录", "warn", "paypal");
      return;
    }
    pushLog(`批量授权启动: ${pending.length} 条 BA`, "info", "paypal");
    try {
      const res = await api("/api/paypal/ba/batch", "POST", {
        ba_tokens: pending.map((r) => r.ba_token),
        config,
      });
      if (res && res.ok) {
        pushLog(`批量授权已启动: ${pending.length} 条`, "ok", "paypal");
      }
    } catch {
      pushLog(`批量授权启动失败 (后端不可用)`, "warn", "paypal");
    }
  };

  const filteredRecords = baRecords.filter(
    (r) => filterStatus === "all" || r.status === filterStatus
  );

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

      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <span className="stat-label">BA 总数</span>
          <div className="stat-value">{stats.total}</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">待授权</span>
          <div className="stat-value" style={{ color: "var(--warn)" }}>{stats.pending}</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">授权中</span>
          <div className="stat-value" style={{ color: "var(--info)" }}>{stats.running}</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">已授权</span>
          <div className="stat-value" style={{ color: "var(--ok)" }}>{stats.success}</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">失败</span>
          <div className="stat-value" style={{ color: "var(--danger)" }}>{stats.failed}</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">成功率</span>
          <div className="stat-value">{successRate}%</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">BA 授权流程</span>
          <span className="card-hint">pm-redirects.stripe.com/authorize → paypal.com/agreements/approve</span>
        </div>
        <div className="card-body">
          <div className="pipeline" style={{ gridTemplateColumns: "repeat(6, 1fr)" }}>
            {STEP_FLOW.map((s) => (
              <div className="stage-cell" key={s.step} style={{ border: "1px solid var(--border)" }}>
                <span className="stage-dot" />
                <span className="stage-name">{BA_STEP_CN[s.step]}</span>
                <span className="stage-try" title={s.desc}>{s.icon}</span>
              </div>
            ))}
          </div>
          <div className="bar-list" style={{ padding: "12px 0 0" }}>
            {STEP_FLOW.map((s) => (
              <div className="bar-row" key={s.step}>
                <span className="bar-label">{BA_STEP_CN[s.step]}</span>
                <span style={{ flex: 1, fontSize: 11.5, color: "var(--text-3)" }}>{s.desc}</span>
              </div>
            ))}
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
                </button>
              ))}
            </div>
          </div>

          {filteredRecords.length === 0 ? (
            <div className="empty">
              <div className="empty-icon">💳</div>
              <div className="empty-title">
                {pendingFromChains.length === 0
                  ? "暂无 BA 记录 — 提链成功后 BA URL 将自动出现在此处"
                  : "暂无匹配记录"}
              </div>
            </div>
          ) : (
            <div className="table-wrap" style={{ border: "none", borderRadius: 0, borderTop: "1px solid var(--border-faint)" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>BA Token</th>
                    <th>邮箱</th>
                    <th>状态</th>
                    <th>当前步骤</th>
                    <th>Captcha</th>
                    <th>出口</th>
                    <th style={{ textAlign: "right" }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRecords.map((r) => (
                    <tr
                      key={r.ba_token}
                      style={{ cursor: "pointer" }}
                      onClick={() => setDetailRecord(r)}
                    >
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
                      <td style={{ textAlign: "right" }}>
                        {r.status === "pending" && (
                          <button
                            className="btn btn-sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleStartAuth(r.ba_token);
                            }}
                          >
                            授权
                          </button>
                        )}
                        {r.status === "running" && <span className="spinner" />}
                        {r.status === "success" && <span style={{ color: "var(--ok)" }}>✓</span>}
                        {r.status === "failed" && <span style={{ color: "var(--danger)" }}>✗</span>}
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
                  <option value="smsbower">SMSBower</option>
                  <option value="sms_activate">SMS-Activate</option>
                  <option value="5sim">5SIM</option>
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">接码价格</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="text"
                  value={config.sms_price}
                  onChange={(e) =>
                    setConfig({ ...config, sms_price: e.target.value })
                  }
                />
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
              <span className="setting-label">出口国家</span>
              <div className="setting-control">
                <select
                  className="select"
                  value={config.exit_country}
                  onChange={(e) =>
                    setConfig({ ...config, exit_country: e.target.value })
                  }
                >
                  <option value="BR">巴西 (BR)</option>
                  <option value="US">美国 (US)</option>
                  <option value="JP">日本 (JP)</option>
                  <option value="GB">英国 (GB)</option>
                  <option value="DE">德国 (DE)</option>
                  <option value="FR">法国 (FR)</option>
                  <option value="CA">加拿大 (CA)</option>
                  <option value="AU">澳大利亚 (AU)</option>
                </select>
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
                  <option value="711_sticky">711 住宅代理 (Sticky)</option>
                  <option value="711_rotate">711 住宅代理 (轮询)</option>
                  <option value="singbox">sing-box 节点</option>
                  <option value="qg_tunnel">QG 隧道</option>
                  <option value="direct">直连</option>
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
                  <option value="dense_signal_reorder_v1">dense_signal_reorder_v1</option>
                  <option value="fraudnet_first">fraudnet_first</option>
                  <option value="tealeaf_reorder">tealeaf_reorder</option>
                  <option value="skip_captcha">skip_captcha (仅 mint)</option>
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">最大重试</span>
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
                  <span className="dr-label">出口国家</span>
                  <span className="dr-value">{detailRecord.country || "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">来源链路</span>
                  <span className="dr-value">{detailRecord.chain_id || "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">SMS 号码</span>
                  <span className="dr-value">{detailRecord.sms_phone || "—"}</span>
                </div>
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
