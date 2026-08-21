import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { PipelineConfig, PipelineStatus } from "../types";
import { CheckIcon, XIcon, WarnIcon } from "../components/icons";

const DEFAULT_CONFIG: PipelineConfig = {
  enabled: false,
  unlimited: true,
  target_accounts: 100,
  tick_interval: 5,
  reg_batch_size: 10,
  reg_email_mode: "",
  reg_cooldown: 30,
  reg_proxy: "",
  reg_country: "auto",
  chain_batch_size: 5,
  chain_concurrent: 3,
  chain_branch: "paypal",
  chain_attempts: 8,
  chain_partial_ok: false,
  pay_max_concurrent: 3,
};

export function PipelineView() {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [config, setConfig] = useState<PipelineConfig>(DEFAULT_CONFIG);
  const [channels, setChannels] = useState<string[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [configSaveState, setConfigSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 加载初始状态 + 配置 + 渠道
  useEffect(() => {
    (async () => {
      try {
        const s = await api<PipelineStatus>("/api/pipeline/status");
        setStatus(s);
        if (s.config) setConfig({ ...DEFAULT_CONFIG, ...s.config });
      } catch { /* ignore */ }
      try {
        const r = await api<{ channels?: string[] }>("/api/register/status");
        if (Array.isArray(r.channels) && r.channels.length) setChannels(r.channels);
      } catch { /* ignore */ }
      try {
        const cr = await api<{ countries?: string[] }>("/api/register/countries");
        if (Array.isArray(cr.countries) && cr.countries.length) setCountries(cr.countries);
      } catch { /* ignore */ }
    })();
  }, []);

  // 轮询守护状态 (3s, 看三段运行态 + 统计实时更新)
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const s = await api<PipelineStatus>("/api/pipeline/status");
        setStatus(s);
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(t);
  }, []);

  // 配置变更自动保存 (1s 防抖)
  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    setConfigSaveState("saving");
    saveTimer.current = setTimeout(async () => {
      try {
        await api("/api/pipeline/config", "POST", config);
        setConfigSaveState("saved");
      } catch {
        setConfigSaveState("error");
      }
    }, 1000);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  const handleStart = async () => {
    try {
      await api("/api/pipeline/start", "POST");
      const s = await api<PipelineStatus>("/api/pipeline/status");
      setStatus(s);
    } catch { /* ignore */ }
  };

  const handleStop = async () => {
    try {
      await api("/api/pipeline/stop", "POST");
      const s = await api<PipelineStatus>("/api/pipeline/status");
      setStatus(s);
    } catch { /* ignore */ }
  };

  const enabled = status?.enabled ?? false;
  const running = status?.running ?? false;
  const stats = status?.stats ?? { reg_started: 0, reg_success: 0, chain_started: 0, chain_success: 0, pay_started: 0, pay_success: 0 };
  const stage = status?.stage_running ?? { reg: false, chain: false, pay: false };
  const payConcurrent = status?.pay_concurrent ?? 0;
  const lastError = status?.last_error ?? "";

  const targetProgress = config.unlimited
    ? null
    : Math.min(100, (stats.pay_success / Math.max(1, config.target_accounts)) * 100);

  return (
    <div>
      <div className="page-head">
        <div>
          <h2 className="page-title">一键流程</h2>
          <p className="page-sub">注册 → 提链 → 支付授权 全链路自动守护，三段并行流水线</p>
        </div>
        <div className="page-actions">
          <button className="btn btn-primary" onClick={handleStart} disabled={running}>
            启动守护
          </button>
          <button className="btn btn-danger" onClick={handleStop} disabled={!enabled}>
            停止守护
          </button>
        </div>
      </div>

      {/* 主开关条 */}
      <div className="card" style={{ marginBottom: 16, padding: "16px 20px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className={`badge ${enabled ? "badge-success" : "badge-muted"}`} style={{ fontSize: 13, padding: "5px 14px" }}>
            {enabled ? "● 运行中" : "○ 已停止"}
          </span>
          {config.unlimited ? (
            <span style={{ color: "var(--text-3)", fontSize: 13 }}>无限模式 · 持续注册产号</span>
          ) : (
            <span style={{ color: "var(--text-3)", fontSize: 13 }}>
              目标 {config.target_accounts} · 已完成 {stats.pay_success}
            </span>
          )}
        </div>
        {targetProgress !== null && (
          <div style={{ flex: 1, maxWidth: 240, display: "flex", alignItems: "center", gap: 8 }}>
            <div className="progress" style={{ flex: 1 }}>
              <div className="progress-bar" style={{ width: `${targetProgress}%` }} />
            </div>
            <span style={{ fontSize: 12, color: "var(--text-3)", minWidth: 36, textAlign: "right" }}>
              {targetProgress.toFixed(0)}%
            </span>
          </div>
        )}
      </div>

      {/* 三段状态卡片 */}
      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <span className="stat-label">
            <span className={`badge ${stage.reg ? "badge-info" : "badge-muted"}`} style={{ fontSize: 11, marginRight: 6 }}>
              {stage.reg ? "运行中" : "空闲"}
            </span>
            注册段
          </span>
          <div className="stat-value">{stats.reg_success}</div>
          <div className="stat-foot">
            <span>已触发 {stats.reg_started} 个</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-label">
            <span className={`badge ${stage.chain ? "badge-info" : "badge-muted"}`} style={{ fontSize: 11, marginRight: 6 }}>
              {stage.chain ? "运行中" : "空闲"}
            </span>
            提链段
          </span>
          <div className="stat-value" style={{ color: "var(--ok)" }}>{stats.chain_success}</div>
          <div className="stat-foot">
            <span>已触发 {stats.chain_started} 个</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-label">
            <span className={`badge ${stage.pay ? "badge-info" : "badge-muted"}`} style={{ fontSize: 11, marginRight: 6 }}>
              {stage.pay ? `${payConcurrent} 运行中` : "空闲"}
            </span>
            支付段
          </span>
          <div className="stat-value" style={{ color: "var(--ok)" }}>{stats.pay_success}</div>
          <div className="stat-foot">
            <span>已触发 {stats.pay_started} 条</span>
          </div>
        </div>
      </div>

      {lastError && (
        <div className="card" style={{ marginBottom: 16, padding: "10px 16px", borderColor: "var(--danger)" }}>
          <span style={{ color: "var(--danger)", fontSize: 13 }}><WarnIcon /> 守护错误: {lastError}</span>
        </div>
      )}

      {/* 守护配置卡片 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">守护配置</span>
          <span className="setting-hint" data-save-state={configSaveState === "idle" ? undefined : configSaveState}>
            {configSaveState === "saving" && "保存中…"}
            {configSaveState === "saved" && "已保存"}
            {configSaveState === "error" && "保存失败"}
          </span>
        </div>
        <div className="card-body">
          {/* 守护参数 */}
          <div className="section-head">
            <span className="section-title">守护参数</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">无限跑</span>
            <div className="setting-control">
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={config.unlimited}
                  onChange={(e) => setConfig({ ...config, unlimited: e.target.checked })}
                />
                <span className={`toggle-slider ${config.unlimited ? "on" : ""}`} />
              </label>
              <span className="setting-hint">
                {config.unlimited ? "持续注册产号, 形成稳态流水线" : "达到目标账号数后自动停止"}
              </span>
            </div>
          </div>
          {!config.unlimited && (
            <div className="setting-row">
              <span className="setting-label">目标账号数</span>
              <div className="setting-control">
                <input
                  className="input"
                  type="number"
                  min={1}
                  value={config.target_accounts}
                  title="支付授权成功数达到此值后自动关停守护"
                  onChange={(e) =>
                    setConfig({ ...config, target_accounts: Math.max(1, parseInt(e.target.value) || 100) })
                  }
                  style={{ width: 84 }}
                />
                <span className="setting-hint">pay_success 达到此值自动停</span>
              </div>
            </div>
          )}
          <div className="setting-row">
            <span className="setting-label">轮询间隔 (秒)</span>
            <div className="setting-control">
              <input
                className="input"
                type="number"
                min={2}
                value={config.tick_interval}
                onChange={(e) =>
                  setConfig({ ...config, tick_interval: Math.max(2, parseInt(e.target.value) || 5) })
                }
                style={{ width: 84 }}
              />
              <span className="setting-hint">守护循环检查三段状态的间隔</span>
            </div>
          </div>

          {/* 注册参数 */}
          <div className="section-head" style={{ marginTop: 16 }}>
            <span className="section-title">注册参数</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">每批数量</span>
            <div className="setting-control">
              <input
                className="input"
                type="number"
                min={1}
                max={200}
                value={config.reg_batch_size}
                onChange={(e) =>
                  setConfig({ ...config, reg_batch_size: Math.max(1, Math.min(200, parseInt(e.target.value) || 10)) })
                }
                style={{ width: 84 }}
              />
              <span className="setting-hint">单批注册账号数 (1-200)</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">邮箱渠道</span>
            <div className="setting-control">
              <select
                className="select"
                value={config.reg_email_mode}
                onChange={(e) => setConfig({ ...config, reg_email_mode: e.target.value })}
              >
                {channels.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <span className="setting-hint">imap:&lt;标签&gt; (邮箱池渠道，留空自动取第一个)</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">冷却时间 (秒)</span>
            <div className="setting-control">
              <input
                className="input"
                type="number"
                min={0}
                value={config.reg_cooldown}
                onChange={(e) =>
                  setConfig({ ...config, reg_cooldown: Math.max(0, parseFloat(e.target.value) || 30) })
                }
                style={{ width: 84 }}
              />
              <span className="setting-hint">账号间注册冷却</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">出口国家</span>
            <div className="setting-control">
              <select
                className="select"
                value={config.reg_country || "auto"}
                onChange={(e) => setConfig({ ...config, reg_country: e.target.value })}
                title="注册出口 IP 国家。auto=随机选; 指定国家则用 711 按该国构造住宅代理"
              >
                <option value="auto">auto (随机)</option>
                {countries.map((cc) => (
                  <option key={cc} value={cc}>{cc}</option>
                ))}
              </select>
              <span className="setting-hint">
                {config.reg_country && config.reg_country !== "auto"
                  ? `按 ${config.reg_country} 出口 IP 注册`
                  : "随机选国家 (与提链国家轮转解耦)"}
              </span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">注册代理</span>
            <div className="setting-control">
              <input
                className="input"
                type="text"
                placeholder="留空 = 自动 711 粘性"
                value={config.reg_proxy}
                onChange={(e) => setConfig({ ...config, reg_proxy: e.target.value })}
                style={{ width: 240 }}
              />
              <span className="setting-hint">留空则自动用 711 粘性 session</span>
            </div>
          </div>

          {/* 提链参数 */}
          <div className="section-head" style={{ marginTop: 16 }}>
            <span className="section-title">提链参数</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">每批数量</span>
            <div className="setting-control">
              <input
                className="input"
                type="number"
                min={1}
                value={config.chain_batch_size}
                onChange={(e) =>
                  setConfig({ ...config, chain_batch_size: Math.max(1, parseInt(e.target.value) || 5) })
                }
                style={{ width: 84 }}
              />
              <span className="setting-hint">单批提链账号数</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">并发上限</span>
            <div className="setting-control">
              <input
                className="input"
                type="number"
                min={1}
                value={config.chain_concurrent}
                onChange={(e) =>
                  setConfig({ ...config, chain_concurrent: Math.max(1, parseInt(e.target.value) || 3) })
                }
                style={{ width: 84 }}
              />
              <span className="setting-hint">提链段独立信号量</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">提链分支</span>
            <div className="setting-control">
              <select
                className="select"
                value={config.chain_branch}
                onChange={(e) => setConfig({ ...config, chain_branch: e.target.value })}
              >
                <option value="paypal">paypal (PayPal BA)</option>
              </select>
              <span className="setting-hint">目前一键流程仅支持 paypal 分支</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">单账号尝试</span>
            <div className="setting-control">
              <input
                className="input"
                type="number"
                min={1}
                value={config.chain_attempts}
                onChange={(e) =>
                  setConfig({ ...config, chain_attempts: Math.max(1, parseInt(e.target.value) || 8) })
                }
                style={{ width: 84 }}
              />
              <span className="setting-hint">单账号提链最大尝试轮数</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">不足也跑</span>
            <div className="setting-control">
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={config.chain_partial_ok}
                  onChange={(e) => setConfig({ ...config, chain_partial_ok: e.target.checked })}
                />
                <span className={`toggle-slider ${config.chain_partial_ok ? "on" : ""}`} />
              </label>
              <span className="setting-hint">
                {config.chain_partial_ok ? "账号不足一批也立即跑" : "攒齐一批再跑 (避免小批次)"}
              </span>
            </div>
          </div>

          {/* 支付参数 */}
          <div className="section-head" style={{ marginTop: 16 }}>
            <span className="section-title">支付参数</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">授权并发上限</span>
            <div className="setting-control">
              <input
                className="input"
                type="number"
                min={1}
                value={config.pay_max_concurrent}
                onChange={(e) =>
                  setConfig({ ...config, pay_max_concurrent: Math.max(1, parseInt(e.target.value) || 3) })
                }
                style={{ width: 84 }}
              />
              <span className="setting-hint">与 _ba_config.max_concurrent 取较小值</span>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16, padding: "12px 20px" }}>
        <span style={{ color: "var(--text-3)", fontSize: 12, lineHeight: 1.8 }}>
          停止为协作式排空: 不杀已运行的注册/提链/支付任务, 它们自然完成后下次不再触发新的。<br />
          支付授权的国家/代理/接码等细节在「PayPal 授权」页配置, 此处仅控制编排层参数。
        </span>
      </div>
    </div>
  );
}
