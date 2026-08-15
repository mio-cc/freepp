import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import type { StageName, StageCfg, BranchName, BranchCfg } from "../types";

/* ==========================================================================
   类型定义
   ========================================================================== */
interface ServerCfg {
  host: string;
  port: number;
  max_concurrent_chains: number;
  thread_pool_size: number;
  chain_mode: string;
  mock_success_rate: number;
  mock_stage_min: number;
  mock_stage_max: number;
}
interface ChainCfg {
  require_zero: boolean;
  auto_billing: boolean;
  token_min_interval_ms: number;
  fail_cooldown_sec: number;
  stages: Partial<Record<StageName, StageCfg>>;
  branches: Partial<Record<BranchName, BranchCfg>>;
}
interface StripeCfg {
  init_version?: string;
  runtime_version?: string;
  checkout_url?: string;
  approve_url?: string;
  init_url_tmpl?: string;
  update_url_tmpl?: string;
  pm_url?: string;
  confirm_url_tmpl?: string;
  poll_url_tmpl?: string;
}
interface TLSCfg {
  impersonate?: string;
  user_agent?: string;
  accept_language?: string;
}
interface ProxyCfg {
  default_pool: string;
  health_check_interval: number;
  max_concurrent_per_node: number;
  qg_super_pool?: { host: string; port: number; auth_key: string; auth_pwd: string };
  qg_resi_pool?: { host: string; port: number; auth_key: string; auth_pwd: string };
  proxy_711?: Record<string, any>;
}
interface MomoPatch {
  name: string;
  desc: string;
  enabled: boolean;
}
interface MomoCfg {
  enabled: boolean;
  patches: MomoPatch[];
}
interface PayPalCfg {
  ba_url_pattern: string;
  pm_redirect_pattern: string;
  blocked_countries: string[];
  success_criteria: string[];
}
interface BillingTemplate {
  country: string;
  name: string;
  city: string;
  state: string;
  postal_code: string;
  line1: string;
  currency: string;
  area_code: number;
}
interface AppConfig {
  server: ServerCfg;
  chain: ChainCfg;
  stripe: StripeCfg;
  tls: TLSCfg;
  proxy: ProxyCfg;
  momo: MomoCfg;
  paypal: PayPalCfg;
}

/* ==========================================================================
   辅助
   ========================================================================== */
const maskKey = (k?: string): string => {
  if (!k) return "—";
  if (k.length <= 8) return "••••";
  return `${k.slice(0, 4)}••••${k.slice(-4)}`;
};

const flag = (cc: string): string => {
  if (!cc || cc.length !== 2) return "";
  const A = 0x1f1e6, Z = 0x1f1ff;
  const c = cc.toUpperCase().charCodeAt(0) - 65;
  const c2 = cc.toUpperCase().charCodeAt(1) - 65;
  if (c < 0 || c > 25 || c2 < 0 || c2 > 25) return "";
  return String.fromCodePoint(A + c, A + c2);
};

/* ==========================================================================
   账单模板行
   ========================================================================== */
function BillingRow({ t }: { t: BillingTemplate }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "36px 60px 1fr 1fr 90px 60px",
        gap: 10,
        padding: "7px 0",
        borderBottom: "1px solid var(--border-faint)",
        alignItems: "center",
        fontSize: 12,
      }}
    >
      <span>{flag(t.country)}</span>
      <span className="tag">{t.country}</span>
      <span>{t.name}</span>
      <span className="muted">{t.city}</span>
      <span className="muted mono">{t.postal_code}</span>
      <span className="tag">{t.currency}</span>
    </div>
  );
}

/* ==========================================================================
   主组件
   ========================================================================== */
export function SettingsView() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [templates, setTemplates] = useState<BillingTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [showAllTemplates, setShowAllTemplates] = useState(false);
  const [billingFilter, setBillingFilter] = useState("");

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const data = await api("/api/config");
      if (data && data.ok) {
        setConfig({
          server: data.server,
          chain: data.chain,
          stripe: data.stripe,
          tls: data.tls,
          proxy: data.proxy,
          momo: data.momo,
          paypal: data.paypal,
        });
      } else {
        setErr((data && data.error) || "加载配置失败");
        setConfig(makeMockConfig());
      }
    } catch {
      setErr("后端未连接，显示默认配置");
      setConfig(makeMockConfig());
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTemplates = useCallback(async () => {
    try {
      const data = await api("/api/billing/templates");
      if (data && data.ok) {
        setTemplates(data.templates);
      }
    } catch {
      // 静默失败
    }
  }, []);

  useEffect(() => {
    loadConfig();
    loadTemplates();
  }, [loadConfig, loadTemplates]);

  if (loading) {
    return (
      <div className="page">
        <div className="page-head">
          <h2 className="page-title">设置</h2>
        </div>
        <div className="card">
          <div className="empty">
            <div className="empty-icon">🔄</div>
            <div className="empty-title">加载中…</div>
          </div>
        </div>
      </div>
    );
  }

  if (!config) {
    return (
      <div className="page">
        <div className="page-head">
          <h2 className="page-title">设置</h2>
        </div>
        <div className="card">
          <div className="empty">
            <div className="empty-title">{err || "暂无配置数据"}</div>
          </div>
        </div>
      </div>
    );
  }

  const server = config.server || ({} as ServerCfg);
  const chain = config.chain || ({} as ChainCfg);
  const stripe = config.stripe || ({} as StripeCfg);
  const tls = config.tls || ({} as TLSCfg);
  const proxy = config.proxy || ({} as ProxyCfg);
  const momo = config.momo || ({ enabled: false, patches: [] } as MomoCfg);
  const momoPatches = momo.patches || [];
  const paypal = config.paypal || ({
    ba_url_pattern: "",
    pm_redirect_pattern: "",
    blocked_countries: [],
    success_criteria: [],
  } as PayPalCfg);

  const filteredTemplates = billingFilter
    ? templates.filter(
        (t) =>
          t.country.toLowerCase().includes(billingFilter.toLowerCase()) ||
          t.name.toLowerCase().includes(billingFilter.toLowerCase())
      )
    : templates;

  const toggleMini = (on: boolean) => (
    <span className={`badge ${on ? "badge-success" : "badge-muted"}`}>
      {on ? "ON" : "OFF"}
    </span>
  );

  return (
    <div className="page">
      {/* 0. GitHub 开源水印 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "10px 16px",
          marginBottom: 14,
          borderRadius: 10,
          border: "1px solid var(--border)",
          background: "linear-gradient(135deg, rgba(88,166,255,0.08), rgba(255,255,255,0.02))",
          fontSize: 12.5,
          color: "var(--text-2)",
        }}
      >
        <span style={{ fontSize: 20, lineHeight: 1 }}>⭐</span>
        <span style={{ flex: 1 }}>
          <span style={{ fontWeight: 600, color: "var(--text-1)" }}>项目已开源</span>
          — 如果你觉得这个项目帮到了你，欢迎去 GitHub 点个 Star 支持一下，顺手 Fork 收藏也感谢 🙏
        </span>
        <a
          href="https://github.com/mio-cc/freepp"
          target="_blank"
          rel="noopener noreferrer"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "5px 12px",
            borderRadius: 999,
            background: "var(--accent)",
            color: "#fff",
            fontSize: 12,
            fontWeight: 600,
            textDecoration: "none",
            whiteSpace: "nowrap",
          }}
        >
          ⭐ github.com/mio-cc/freepp
        </a>
      </div>

      <div className="page-head">
        <div>
          <h2 className="page-title">设置</h2>
          <p className="page-sub">
            账单国 · PayPal 授权 · Stripe 指纹 · TLS · 代理 · MoMo 补丁 · 服务器
            {err && <span style={{ color: "var(--warn)" }}> ({err})</span>}
          </p>
        </div>
        <div className="page-actions">
          <button className="btn btn-sm" onClick={loadConfig}>
            刷新
          </button>
        </div>
      </div>

      {/* 1. 账单国配置 */}
      <div className="card settings-panel">
        <div className="card-head">
          <span className="card-title">账单国配置</span>
          <span className="card-hint">Payment Method billing_details · 贴近出口国</span>
        </div>
        <div className="card-body">
          <div className="setting-row">
            <span className="setting-label">自动账单贴近</span>
            <div className="setting-control">
              {toggleMini(chain.auto_billing)}
              <span className="muted" style={{ fontSize: 11.5 }}>
                {chain.auto_billing ? "账单地址跟随 provider 段出口国" : "使用固定账单国"}
              </span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">账单模板数</span>
            <div className="setting-control">
              <span className="badge badge-accent">{templates.length || "—"}</span>
              <span className="muted" style={{ fontSize: 11.5 }}>个国家可用</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">Token 间隔</span>
            <div className="setting-control">
              <span className="tag">{chain.token_min_interval_ms}ms</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">失败冷却</span>
            <div className="setting-control">
              <span className="tag">{chain.fail_cooldown_sec}s</span>
            </div>
          </div>
        </div>

        <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
          <div className="section-head">
            <span className="section-title">账单模板</span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setShowAllTemplates(!showAllTemplates)}
            >
              {showAllTemplates ? "收起" : `展开全部 (${templates.length})`}
            </button>
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <input
              className="input"
              style={{ width: 240 }}
              type="search"
              placeholder="搜索国家代码或姓名…"
              value={billingFilter}
              onChange={(e) => setBillingFilter(e.target.value)}
            />
          </div>
          {showAllTemplates && (
            <div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "36px 60px 1fr 1fr 90px 60px",
                  gap: 10,
                  padding: "7px 0",
                  fontSize: 10,
                  fontWeight: 600,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--text-3)",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <span></span>
                <span>国家</span>
                <span>姓名</span>
                <span>城市</span>
                <span>邮编</span>
                <span>币种</span>
              </div>
              {filteredTemplates.length > 0 ? (
                filteredTemplates.map((t) => <BillingRow key={t.country} t={t} />)
              ) : (
                <div className="empty" style={{ padding: 16 }}>无匹配模板</div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 3. PayPal 支付授权 */}
      <div className="card settings-panel">
        <div className="card-head">
          <span className="card-title">PayPal 支付授权</span>
          <span className="card-hint">BA (Billing Agreement) Approve · 提链目标</span>
        </div>
        <div className="card-body">
          <div className="section-head">
            <span className="section-title">成功判定（三条件同时满足）</span>
          </div>
          <div className="bar-list" style={{ padding: "4px 0 10px" }}>
            {paypal.success_criteria.map((c, i) => (
              <div className="bar-row" key={i}>
                <span className="patch-idx">{i + 1}</span>
                <span style={{ fontSize: 12, color: "var(--text-2)" }}>{c}</span>
              </div>
            ))}
          </div>
          <div className="setting-row">
            <span className="setting-label">BA URL 模式</span>
            <div className="setting-control">
              <code className="tag" style={{ wordBreak: "break-all" }}>{paypal.ba_url_pattern}</code>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">PM Redirect 模式</span>
            <div className="setting-control">
              <code className="tag" style={{ wordBreak: "break-all" }}>{paypal.pm_redirect_pattern}</code>
            </div>
          </div>
        </div>
        <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
          <div className="section-head">
            <span className="section-title">Stripe API 端点</span>
          </div>
          <div className="mini-grid" style={{ padding: 0 }}>
            <div className="mini-card">
              <div className="mini-card-label">Checkout</div>
              <div className="mini-card-value" style={{ fontSize: 11 }}>{stripe.checkout_url || "—"}</div>
            </div>
            <div className="mini-card">
              <div className="mini-card-label">Approve</div>
              <div className="mini-card-value" style={{ fontSize: 11 }}>{stripe.approve_url || "—"}</div>
            </div>
            <div className="mini-card">
              <div className="mini-card-label">Payment Method</div>
              <div className="mini-card-value" style={{ fontSize: 11 }}>{stripe.pm_url || "—"}</div>
            </div>
            <div className="mini-card">
              <div className="mini-card-label">Confirm</div>
              <div className="mini-card-value" style={{ fontSize: 11 }}>{stripe.confirm_url_tmpl || "—"}</div>
            </div>
            <div className="mini-card">
              <div className="mini-card-label">Poll</div>
              <div className="mini-card-value" style={{ fontSize: 11 }}>{stripe.poll_url_tmpl || "—"}</div>
            </div>
          </div>
        </div>
        <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
          <div className="section-head">
            <span className="section-title">PayPal 不支持 / 风控高危国家</span>
          </div>
          <div className="country-tags">
            {paypal.blocked_countries.map((c) => (
              <span key={c} className="country-tag country-tag-blocked">
                {flag(c)} {c}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* 4. Stripe Init 指纹 */}
      <div className="card settings-panel">
        <div className="card-head">
          <span className="card-title">Stripe Init 指纹</span>
          <span className="card-hint">payment_pages init 版本 · 运行时</span>
        </div>
        <div className="mini-grid">
          <div className="mini-card">
            <div className="mini-card-label">Stripe Init Version</div>
            <div className="mini-card-value" style={{ fontSize: 11 }}>{stripe.init_version || "—"}</div>
            <div className="mini-card-desc">Stripe payment_pages init API 版本指纹</div>
          </div>
          <div className="mini-card">
            <div className="mini-card-label">Init URL 模板</div>
            <div className="mini-card-value" style={{ fontSize: 11 }}>
              {stripe.init_url_tmpl || "https://api.stripe.com/v1/payment_pages/{cs}/init"}
            </div>
            <div className="mini-card-desc">{"{cs}"} = checkout_session_id</div>
          </div>
          <div className="mini-card">
            <div className="mini-card-label">Runtime Version</div>
            <div className="mini-card-value">{stripe.runtime_version || "—"}</div>
            <div className="mini-card-desc">stripe.js 运行时版本</div>
          </div>
          <div className="mini-card">
            <div className="mini-card-label">Update URL 模板</div>
            <div className="mini-card-value" style={{ fontSize: 11 }}>
              {stripe.update_url_tmpl || "https://api.stripe.com/v1/payment_pages/{cs}/update"}
            </div>
            <div className="mini-card-desc">S3 金额守卫段 (update) 请求地址</div>
          </div>
        </div>
      </div>

      {/* 5. TLS 指纹 */}
      <div className="card settings-panel">
        <div className="card-head">
          <span className="card-title">TLS 指纹</span>
          <span className="card-hint">curl_cffi impersonate</span>
        </div>
        <div className="card-body">
          <div className="setting-row">
            <span className="setting-label">impersonate</span>
            <div className="setting-control">
              <code className="tag">{tls.impersonate || "chrome"}</code>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">User-Agent</span>
            <div className="setting-control">
              <code className="tag" style={{ wordBreak: "break-all" }}>{tls.user_agent || "—"}</code>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">Accept-Language</span>
            <div className="setting-control">
              <code className="tag">{tls.accept_language || "—"}</code>
            </div>
          </div>
        </div>
      </div>

      {/* 6. 代理配置 */}
      <div className="card settings-panel">
        <div className="card-head">
          <span className="card-title">代理配置</span>
          <span className="card-hint">青果隧道 · 711 代理池</span>
        </div>
        <div className="card-body">
          <div className="setting-row">
            <span className="setting-label">默认池</span>
            <div className="setting-control">
              <code className="tag">{proxy.default_pool}</code>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">健康检查间隔</span>
            <div className="setting-control"><span className="tag">{proxy.health_check_interval}s</span></div>
          </div>
          <div className="setting-row">
            <span className="setting-label">每节点最大并发</span>
            <div className="setting-control"><span className="tag">{proxy.max_concurrent_per_node}</span></div>
          </div>
          {proxy.qg_resi_pool && (
            <div className="setting-row">
              <span className="setting-label">住宅池 (resi)</span>
              <div className="setting-control">
                <code className="tag">{proxy.qg_resi_pool.host}:{proxy.qg_resi_pool.port}</code>
                <span className="muted" style={{ fontSize: 11.5 }}>
                  key: {maskKey(proxy.qg_resi_pool.auth_key)}
                </span>
              </div>
            </div>
          )}
          {proxy.qg_super_pool && (
            <div className="setting-row">
              <span className="setting-label">机房池 (super)</span>
              <div className="setting-control">
                <code className="tag">{proxy.qg_super_pool.host}:{proxy.qg_super_pool.port}</code>
                <span className="muted" style={{ fontSize: 11.5 }}>
                  key: {maskKey(proxy.qg_super_pool.auth_key)}
                </span>
              </div>
            </div>
          )}
          {proxy.proxy_711 && proxy.proxy_711.enabled && (
            <div className="setting-row">
              <span className="setting-label">711 代理池</span>
              <div className="setting-control">
                <span className="badge badge-accent">已启用</span>
                <span className="muted" style={{ fontSize: 11.5 }}>
                  relay: {proxy.proxy_711.relay_base}:{proxy.proxy_711.relay_port_start}-
                  {proxy.proxy_711.relay_port_end}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 7. MoMo 补丁 */}
      <div className="card settings-panel">
        <div className="card-head">
          <span className="card-title">MoMo 提链补丁</span>
          <span className="card-hint">五层 Patch · {momo.enabled ? "已启用" : "未启用"}</span>
        </div>
        <div className="patch-list">
          {momoPatches.map((p, i) => (
            <div className="patch-row" key={p.name}>
              <div className="patch-meta">
                <span className="patch-idx">{i + 1}</span>
                <div className="patch-text">
                  <div className="patch-name">
                    {p.name}{" "}
                    <span className={`badge ${p.enabled ? "badge-success" : "badge-muted"}`}>
                      {p.enabled ? "已启用" : "未启用"}
                    </span>
                  </div>
                  <div className="patch-desc">{p.desc}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 8. 服务器配置 */}
      <div className="card settings-panel">
        <div className="card-head">
          <span className="card-title">服务器配置</span>
          <span className="card-hint">FastAPI · {server.chain_mode} 模式</span>
        </div>
        <div className="card-body">
          <div className="setting-row">
            <span className="setting-label">监听地址</span>
            <div className="setting-control">
              <code className="tag">{server.host}:{server.port}</code>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">最大并发链路</span>
            <div className="setting-control"><span className="tag">{server.max_concurrent_chains}</span></div>
          </div>
          <div className="setting-row">
            <span className="setting-label">线程池大小</span>
            <div className="setting-control"><span className="tag">{server.thread_pool_size}</span></div>
          </div>
          <div className="setting-row">
            <span className="setting-label">链路模式</span>
            <div className="setting-control">
              <span className={`badge ${server.chain_mode === "live" ? "badge-success" : "badge-warn"}`}>
                {server.chain_mode}
              </span>
              {server.chain_mode === "mock" && (
                <span className="muted" style={{ fontSize: 11.5 }}>
                  成功率 {Math.round(server.mock_success_rate * 100)}%
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="muted" style={{ marginTop: 12, fontSize: 12 }}>
        系统级配置；各提链分支（PayPal 提炼 / MoMo 提链 / Grok 链路 / PIX 二维码）的
        七段出口与开关在各链路页内独立配置
      </div>
    </div>
  );
}

/* ==========================================================================
   Mock 配置 (后端离线时使用)
   ========================================================================== */
function makeMockConfig(): AppConfig {
  const mkStages = (cc: string[]): Partial<Record<StageName, StageCfg>> => ({
    checkout: { countries: cc, timeout: 15, retry: 3 },
    init: { countries: cc, timeout: 10, retry: 3 },
    update: { countries: cc, timeout: 10, retry: 3 },
    provider: { countries: cc, timeout: 8, retry: 3 },
    approve: { countries: cc, timeout: 6, retry: 3 },
    poll: { countries: cc, timeout: 25, retry: 1, poll_interval: 0.75, max_polls: 40 },
    resolve: { countries: cc, timeout: 20, retry: 2 },
  });

  const mkBranch = (
    name: BranchName,
    label: string,
    channel: string,
    token_source: string,
    cc: string[],
    extra: Partial<BranchCfg> = {}
  ): BranchCfg => ({
    name,
    label,
    channel,
    token_source,
    require_zero: true,
    channel_check: true,
    dual_init: false,
    init0_ccs: cc.slice(0, 1),
    init1_ccs: cc,
    init_t_ccs: [],
    follow_checkout: false,
    billing_country: "auto",
    attempts: 8,
    stages: mkStages(cc),
    ...extra,
  });

  return {
    server: {
      host: "0.0.0.0",
      port: 8770,
      max_concurrent_chains: 10,
      thread_pool_size: 20,
      chain_mode: "mock",
      mock_success_rate: 0.6,
      mock_stage_min: 0.4,
      mock_stage_max: 1.6,
    },
    chain: {
      require_zero: true,
      auto_billing: true,
      token_min_interval_ms: 500,
      fail_cooldown_sec: 60,
      stages: mkStages(["US", "GB"]),
      branches: {
        paypal: mkBranch("paypal", "PayPal 提炼", "paypal", "stripe", ["US", "GB", "AU"]),
        momo: mkBranch("momo", "MoMo 提链", "momo", "momo", ["VN"], {
          require_zero: false,
          dual_init: true,
          follow_checkout: true,
        }),
        grok: mkBranch("grok", "Grok 链路", "card", "grok", ["US"], {
          require_zero: false,
          follow_checkout: true,
        }),
        pix: mkBranch("pix", "PIX 二维码", "link", "pix", ["BR"], {
          follow_checkout: true,
        }),
      },
    },
    stripe: {
      init_version: "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1",
      runtime_version: "6f8494a281",
      checkout_url: "https://chatgpt.com/backend-api/payments/checkout",
      approve_url: "https://chatgpt.com/backend-api/payments/checkout/approve",
      init_url_tmpl: "https://api.stripe.com/v1/payment_pages/{cs}/init",
      update_url_tmpl: "https://api.stripe.com/v1/payment_pages/{cs}/update",
      pm_url: "https://api.stripe.com/v1/payment_methods",
      confirm_url_tmpl: "https://api.stripe.com/v1/payment_pages/{cs}/confirm",
      poll_url_tmpl: "https://api.stripe.com/v1/payment_pages/{cs}",
    },
    tls: {
      impersonate: "chrome",
      user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
      accept_language: "en-US,en;q=0.9",
    },
    proxy: {
      default_pool: "qg_resi_pool",
      health_check_interval: 30,
      max_concurrent_per_node: 3,
      qg_super_pool: { host: "overseas.tunnel.qg.net", port: 16629, auth_key: "VT****KP", auth_pwd: "6B****EF" },
      qg_resi_pool: { host: "overseas.tunnel.qg.net", port: 14408, auth_key: "VX****1B", auth_pwd: "9D****1C" },
      proxy_711: { enabled: true, relay_base: "127.0.0.1", clash_port: 7897, relay_port_start: 18077, relay_port_end: 18117 },
    },
    momo: {
      enabled: false,
      patches: [
        { name: "connect_intercept", desc: "L1: 拦截 api.stripe.com CONNECT", enabled: true },
        { name: "dns_fix", desc: "L2: Clash fake-ip DoH 重解析", enabled: true },
        { name: "pm_inject", desc: "L3: payment_method 注入", enabled: true },
        { name: "confirm_build", desc: "L4: confirm payload 构造", enabled: true },
        { name: "resolve_regex", desc: "L5: MoMo 支付 URL 正则", enabled: true },
      ],
    },
    paypal: {
      ba_url_pattern: "https://www.paypal.com/agreements/approve?ba_token=...",
      pm_redirect_pattern: "https://pm-redirects.stripe.com/authorize/...",
      blocked_countries: ["AF", "BY", "CU", "EG", "IR", "KP", "LY", "MM", "RU", "SD", "SO", "SS", "SY", "YE"],
      success_criteria: [
        "init.invoice.amount_due == 0 (零金额)",
        "redirect 匹配 pm-redirects.stripe.com/authorize/",
        "最终 URL 匹配 paypal.com/agreements/approve?ba_token=",
      ],
    },
  };
}
