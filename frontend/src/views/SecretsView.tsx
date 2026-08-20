import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";

/* ==========================================================================
   密钥与凭据页 — 把分散在 config.yaml / secrets.json / .env / 环境变量的
   API key、平台凭据、端点参数集中到前端可编辑, 写入后端落盘并热生效。
   离线开源项目: 无需鉴权, 凭据原值可见 (密码框 type=password 防肩窥)。
   ========================================================================== */

// ---- secrets.json 字段 (B 层: env 注入 + 热重载) ----
interface Seven11Secrets {
  PROXY_711_HOST: string;
  PROXY_711_PORT: string;
  PROXY_711_USER: string;
  PROXY_711_PASS: string;
  CLASH_PROXY: string;
  PROXY_711_RELAY_PORT: string;
  PROXY_711_CONNECT_REWRITE_HOSTS: string;
}
interface Api798Secrets {
  REG_API798_MAILBOXES: string;
  REG_API798_ENDPOINT: string;
  REG_API798_ENABLED: string;
}
interface SmsSecrets { SMSBOWER_API_KEY: string; GRIZZLYSMS_API_KEY: string }
interface PaypalAntibotSecrets {
  PAYPAL_ROXY_API_KEY: string;
  PAYPAL_DATADOME_MODE: string;
  PAYPAL_MTR_RUNTIME: string;
  PAYPAL_MTR_CHANNEL: string;
  PAYPAL_MTR_API_KEY: string;
  PAYPAL_RISK_SIGNALS_MODE: string;
  PAYPAL_FINGERPRINT_SOURCE: string;
  PAYPAL_HCAPTCHA_TOKEN: string;
}
type SecretsData = {
  seven11: Seven11Secrets;
  api798: Api798Secrets;
  sms: SmsSecrets;
  paypal_antibot: PaypalAntibotSecrets;
};

// ---- config.yaml A 层标量 (POST /api/config/section) ----
interface ProxyPool { host: string; port: number; auth_key: string; auth_pwd: string }
interface ConfigScalars {
  server: { host: string; port: number; max_concurrent_chains: number; thread_pool_size: number; chain_mode: string; mock_success_rate: number; mock_stage_min: number; mock_stage_max: number };
  stripe: Record<string, string>;
  tls: { impersonate: string; user_agent: string; accept_language: string };
  proxy: { default_pool: string; health_check_interval: number; max_concurrent_per_node: number; sess_time: number };
  register_pool: { base_url: string; timeout: number };
  storage: { db_path: string; samples_dir: string; runs_dir: string };
  geo: { enabled: boolean; timeout: number; sources: string[] };
  logging: { level: string; json_logs: boolean };
  momo: { enabled: boolean; connect_intercept: boolean; dns_fix: boolean; pm_inject: boolean; confirm_build: boolean; resolve_regex: boolean };
  proxyPools: { qg_super_pool: ProxyPool; qg_resi_pool: ProxyPool; default_pool: string };
}

type SecretSection = keyof SecretsData;
type ConfigSection = "server" | "stripe" | "tls" | "proxy" | "register_pool" | "storage" | "geo" | "logging" | "momo";

const EMPTY_SECRETS: SecretsData = {
  seven11: { PROXY_711_HOST: "", PROXY_711_PORT: "", PROXY_711_USER: "", PROXY_711_PASS: "", CLASH_PROXY: "", PROXY_711_RELAY_PORT: "", PROXY_711_CONNECT_REWRITE_HOSTS: "" },
  api798: { REG_API798_MAILBOXES: "", REG_API798_ENDPOINT: "", REG_API798_ENABLED: "1" },
  sms: { SMSBOWER_API_KEY: "", GRIZZLYSMS_API_KEY: "" },
  paypal_antibot: { PAYPAL_ROXY_API_KEY: "", PAYPAL_DATADOME_MODE: "", PAYPAL_MTR_RUNTIME: "", PAYPAL_MTR_CHANNEL: "", PAYPAL_MTR_API_KEY: "", PAYPAL_RISK_SIGNALS_MODE: "", PAYPAL_FINGERPRINT_SOURCE: "", PAYPAL_HCAPTCHA_TOKEN: "" },
};

export function SecretsView() {
  const setView = useStore((s) => s.setView);
  const [secrets, setSecrets] = useState<SecretsData>(EMPTY_SECRETS);
  const [cfg, setCfg] = useState<ConfigScalars | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [savedFlash, setSavedFlash] = useState("");

  // ---- 加载 ----
  useEffect(() => {
    (async () => {
      try {
        const [secRes, cfgRes] = await Promise.all([
          api("/api/config/secrets", "GET"),
          api("/api/config", "GET"),
        ]);
        if (secRes?.secrets) setSecrets((prev) => ({ ...prev, ...secRes.secrets }));
        if (cfgRes) {
          setCfg({
            server: cfgRes.server,
            stripe: cfgRes.stripe || {},
            tls: cfgRes.tls,
            proxy: cfgRes.proxy,
            register_pool: cfgRes.register_pool,
            storage: cfgRes.storage,
            geo: cfgRes.geo,
            logging: cfgRes.logging,
            momo: cfgRes.momo,
            proxyPools: {
              qg_super_pool: secRes?.proxy_pools?.qg_super_pool || { host: "", port: 0, auth_key: "", auth_pwd: "" },
              qg_resi_pool: secRes?.proxy_pools?.qg_resi_pool || { host: "", port: 0, auth_key: "", auth_pwd: "" },
              default_pool: secRes?.proxy_pools?.default_pool || "",
            },
          });
        }
      } catch (e: any) {
        setErr(e?.message || "加载失败");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // ---- 自动保存 (1s 防抖, 复用 PayPalView 模式) ----
  const saveSecretsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (loading) return;
    if (saveSecretsTimer.current) clearTimeout(saveSecretsTimer.current);
    saveSecretsTimer.current = setTimeout(async () => {
      // 找出与上次不同的 section 整组提交 (后端 update 只改非空 diff)
      for (const sec of Object.keys(secrets) as SecretSection[]) {
        try {
          await api("/api/config/secrets", "POST", { section: sec, fields: secrets[sec] });
        } catch { /* ignore */ }
      }
      setSavedFlash("已保存 ✓");
      setTimeout(() => setSavedFlash(""), 1500);
    }, 1000);
    return () => { if (saveSecretsTimer.current) clearTimeout(saveSecretsTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secrets]);

  const saveCfgTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleCfgSave = (section: ConfigSection, fields: Record<string, unknown>) => {
    if (saveCfgTimer.current) clearTimeout(saveCfgTimer.current);
    saveCfgTimer.current = setTimeout(async () => {
      try {
        await api("/api/config/section", "POST", { section, fields });
        setSavedFlash("已保存 ✓");
        setTimeout(() => setSavedFlash(""), 1500);
      } catch { /* ignore */ }
    }, 1000);
  };

  // ---- helpers ----
  const updSecret = (sec: SecretSection, fld: string, val: string) =>
    setSecrets((prev) => ({ ...prev, [sec]: { ...prev[sec], [fld]: val } }));

  const flash = () => savedFlash && (
    <span className="muted" style={{ fontSize: 11.5, color: "var(--ok)" }}>{savedFlash}</span>
  );

  if (loading) {
    return (
      <div className="page">
        <div className="page-head"><h2 className="page-title">密钥与凭据</h2><p className="page-sub">加载中…</p></div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">密钥与凭据</h2>
          <p className="page-sub">
            代理凭据 · 注册功能 · SMS 接码 · PayPal 反爬 · Stripe 端点 · TLS 指纹 · 服务器 · MoMo · 存储
            {err && <span style={{ color: "var(--warn)" }}> ({err})</span>}
          </p>
        </div>
        <div className="page-actions">
          {flash()}
          <button className="btn btn-ghost btn-sm" onClick={() => setView("settings")}>← 返回设置</button>
        </div>
      </div>

      {/* 1. 代理凭据 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">代理凭据</span>
          <span className="card-hint">青果 QG 池 · 711 住宅代理 · 写入后热生效</span>
        </div>
        <div className="card-body">
          <div className="section-head"><span className="section-title">青果隧道池 (QG)</span></div>
          <PoolRow label="住宅池 (resi)" pool={cfg?.proxyPools.qg_resi_pool} onChange={(p) => { setCfg(c => c ? { ...c, proxyPools: { ...c.proxyPools, qg_resi_pool: p } } : c); scheduleCfgSave("proxy", { qg_resi_pool: p }); }} />
          <PoolRow label="机房池 (super)" pool={cfg?.proxyPools.qg_super_pool} onChange={(p) => { setCfg(c => c ? { ...c, proxyPools: { ...c.proxyPools, qg_super_pool: p } } : c); scheduleCfgSave("proxy", { qg_super_pool: p }); }} />
          <div className="setting-row">
            <span className="setting-label">默认池</span>
            <div className="setting-control">
              <select className="select" value={cfg?.proxyPools.default_pool || ""} onChange={(e) => { const v = e.target.value; setCfg(c => c ? { ...c, proxyPools: { ...c.proxyPools, default_pool: v } } : c); scheduleCfgSave("proxy", { default_pool: v }); }} style={{ width: 180 }}>
                <option value="">(无)</option>
                <option value="qg_resi_pool">住宅池 qg_resi_pool</option>
                <option value="qg_super_pool">机房池 qg_super_pool</option>
              </select>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">健康检查间隔</span>
            <div className="setting-control">
              <input className="input" type="number" value={cfg?.proxy.health_check_interval ?? ""} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, proxy: { ...c.proxy, health_check_interval: v } } : c); scheduleCfgSave("proxy", { health_check_interval: v }); }} style={{ width: 100 }} /> <span className="muted" style={{ fontSize: 11.5 }}>秒</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">每节点最大并发</span>
            <div className="setting-control">
              <input className="input" type="number" value={cfg?.proxy.max_concurrent_per_node ?? ""} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, proxy: { ...c.proxy, max_concurrent_per_node: v } } : c); scheduleCfgSave("proxy", { max_concurrent_per_node: v }); }} style={{ width: 100 }} />
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">711 会话保持</span>
            <div className="setting-control">
              <input className="input" type="number" value={cfg?.proxy.sess_time ?? ""} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, proxy: { ...c.proxy, sess_time: v } } : c); scheduleCfgSave("proxy", { sess_time: v }); }} style={{ width: 100 }} /> <span className="muted" style={{ fontSize: 11.5 }}>秒</span>
            </div>
          </div>

          <div className="section-head" style={{ marginTop: 8 }}><span className="section-title">711 住宅代理</span></div>
          <SecretRow label="网关 Host" value={secrets.seven11.PROXY_711_HOST} onChange={(v) => updSecret("seven11", "PROXY_711_HOST", v)} placeholder="global.rotgb.711proxy.com" />
          <SecretRow label="网关端口" value={secrets.seven11.PROXY_711_PORT} onChange={(v) => updSecret("seven11", "PROXY_711_PORT", v)} placeholder="10000" />
          <SecretRow label="用户名" value={secrets.seven11.PROXY_711_USER} onChange={(v) => updSecret("seven11", "PROXY_711_USER", v)} placeholder="YOUR_711_USER" />
          <SecretRow label="密码" value={secrets.seven11.PROXY_711_PASS} onChange={(v) => updSecret("seven11", "PROXY_711_PASS", v)} placeholder="YOUR_711_PASS" password />
          <SecretRow label="Clash 本地代理" value={secrets.seven11.CLASH_PROXY} onChange={(v) => updSecret("seven11", "CLASH_PROXY", v)} placeholder="127.0.0.1:7890" />
          <SecretRow label="中继端口" value={secrets.seven11.PROXY_711_RELAY_PORT} onChange={(v) => updSecret("seven11", "PROXY_711_RELAY_PORT", v)} placeholder="18794" />
          <SecretRow label="CONNECT 改写主机" value={secrets.seven11.PROXY_711_CONNECT_REWRITE_HOSTS} onChange={(v) => updSecret("seven11", "PROXY_711_CONNECT_REWRITE_HOSTS", v)} placeholder="www.paypal.com" hint="逗号分隔" />
        </div>
      </div>

      {/* 2. 注册功能 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">注册功能</span>
          <span className="card-hint">邮箱渠道 · codex_register 注册池（全部可在前端配置，不再硬编码）</span>
        </div>
        <div className="card-body">
          <div className="section-head"><span className="section-title">api798 邮箱卡密</span></div>
          <SecretSelectRow label="启用状态" value={secrets.api798.REG_API798_ENABLED || "1"} onChange={(v) => updSecret("api798", "REG_API798_ENABLED", v)} options={[["1", "启用 (默认)"], ["0", "禁用"]]} />
          <SecretRow label="卡密文件路径" value={secrets.api798.REG_API798_MAILBOXES} onChange={(v) => updSecret("api798", "REG_API798_MAILBOXES", v)} placeholder="C:\\path\\to\\mailboxes.txt" hint="每行 email----auth_code, 启动时加载" />
          <SecretRow label="取码端点" value={secrets.api798.REG_API798_ENDPOINT} onChange={(v) => updSecret("api798", "REG_API798_ENDPOINT", v)} placeholder="https://api798.com/get_code" hint="留空使用默认端点" />
          <div className="section-head" style={{ marginTop: 8 }}><span className="section-title">codex_register 注册池</span></div>
          <div className="setting-row">
            <span className="setting-label">注册池地址</span>
            <div className="setting-control">
              <input className="input" value={cfg?.register_pool.base_url ?? ""} onChange={(e) => { const v = e.target.value; setCfg(c => c ? { ...c, register_pool: { ...c.register_pool, base_url: v } } : c); scheduleCfgSave("register_pool", { base_url: v }); }} placeholder="http://127.0.0.1:8780" style={{ width: 280 }} />
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">超时</span>
            <div className="setting-control">
              <input className="input" type="number" value={cfg?.register_pool.timeout ?? ""} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, register_pool: { ...c.register_pool, timeout: v } } : c); scheduleCfgSave("register_pool", { timeout: v }); }} style={{ width: 100 }} /> <span className="muted" style={{ fontSize: 11.5 }}>秒</span>
            </div>
          </div>
        </div>
      </div>

      {/* 2b. 邮箱域名池 (PayPal 注册邮箱域名, 按国家配置) */}
      <EmailDomainsCard />

      {/* 3. SMS 接码 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">SMS 接码</span>
          <span className="card-hint">全局默认 key · PayPal 授权页留空时回落到这里</span>
        </div>
        <div className="card-body">
          <SecretRow label="SMSBower API Key" value={secrets.sms.SMSBOWER_API_KEY} onChange={(v) => updSecret("sms", "SMSBOWER_API_KEY", v)} password placeholder="留空使用 .env / 默认" />
          <SecretRow label="GrizzlySMS API Key" value={secrets.sms.GRIZZLYSMS_API_KEY} onChange={(v) => updSecret("sms", "GRIZZLYSMS_API_KEY", v)} password placeholder="留空使用 .env / 默认" />
        </div>
      </div>

      {/* 4. PayPal 反爬 / 指纹 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">PayPal 反爬 / 指纹</span>
          <span className="card-hint">Roxy · DataDome · MTR · Risk · hCaptcha</span>
        </div>
        <div className="card-body">
          <SecretRow label="Roxy API Key" value={secrets.paypal_antibot.PAYPAL_ROXY_API_KEY} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_ROXY_API_KEY", v)} password placeholder="本地 Roxy 浏览器 API key" />
          <SecretSelectRow label="指纹来源" value={secrets.paypal_antibot.PAYPAL_FINGERPRINT_SOURCE} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_FINGERPRINT_SOURCE", v)} options={[["random", "random (默认)"], ["roxy", "roxy (Roxy 浏览器)"], ["auto", "auto (有 key 用 roxy)"]]} />
          <SecretSelectRow label="DataDome 模式" value={secrets.paypal_antibot.PAYPAL_DATADOME_MODE} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_DATADOME_MODE", v)} options={[["protocol", "protocol (默认)"], ["roxy", "roxy"], ["headless", "headless"], ["auto", "auto"], ["off", "off"]]} />
          <SecretSelectRow label="MTR 来源" value={secrets.paypal_antibot.PAYPAL_MTR_RUNTIME} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_MTR_RUNTIME", v)} options={[["python_generated", "python_generated (默认)"], ["roxy", "roxy"]]} />
          <SecretRow label="MTR Channel" value={secrets.paypal_antibot.PAYPAL_MTR_CHANNEL} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_MTR_CHANNEL", v)} placeholder="iwc-mxo" />
          <SecretRow label="MTR API Key" value={secrets.paypal_antibot.PAYPAL_MTR_API_KEY} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_MTR_API_KEY", v)} password placeholder="留空使用默认" />
          <SecretSelectRow label="Risk Signals 模式" value={secrets.paypal_antibot.PAYPAL_RISK_SIGNALS_MODE} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_RISK_SIGNALS_MODE", v)} options={[["protocol", "protocol (默认)"], ["roxy", "roxy"]]} />
          <SecretRow label="hCaptcha Token" value={secrets.paypal_antibot.PAYPAL_HCAPTCHA_TOKEN} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_HCAPTCHA_TOKEN", v)} password placeholder="留空使用默认" />
        </div>
      </div>

      {/* 5. Stripe 端点 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">Stripe 端点</span>
            <span className="card-hint">init / update / confirm / poll · chatgpt.com checkout</span>
          </div>
          <div className="card-body">
            <CfgTextRow label="Checkout URL" section="stripe" field="checkout_url" value={cfg.stripe.checkout_url ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://chatgpt.com/..." />
            <CfgTextRow label="Approve URL" section="stripe" field="approve_url" value={cfg.stripe.approve_url ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://chatgpt.com/..." />
            <CfgTextRow label="Payment Methods URL" section="stripe" field="pm_url" value={cfg.stripe.pm_url ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/..." />
            <CfgTextRow label="Init URL 模板" section="stripe" field="init_url_tmpl" value={cfg.stripe.init_url_tmpl ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/v1/invoices/{cs}/init" />
            <CfgTextRow label="Update URL 模板" section="stripe" field="update_url_tmpl" value={cfg.stripe.update_url_tmpl ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/v1/invoices/{cs}/update" />
            <CfgTextRow label="Confirm URL 模板" section="stripe" field="confirm_url_tmpl" value={cfg.stripe.confirm_url_tmpl ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/v1/invoices/{cs}/confirm" />
            <CfgTextRow label="Poll URL 模板" section="stripe" field="poll_url_tmpl" value={cfg.stripe.poll_url_tmpl ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/v1/invoices/{cs}/poll" />
            <CfgTextRow label="Init 版本" section="stripe" field="init_version" value={cfg.stripe.init_version ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="2025-08-27" />
            <CfgTextRow label="Runtime 版本" section="stripe" field="runtime_version" value={cfg.stripe.runtime_version ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="2025-08-27" />
          </div>
        </div>
      )}

      {/* 6. TLS 指纹 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">TLS 指纹</span>
            <span className="card-hint">curl_cffi impersonate · UA · 语言</span>
          </div>
          <div className="card-body">
            <CfgTextRow label="impersonate" section="tls" field="impersonate" value={cfg.tls.impersonate} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="chrome146" />
            <CfgTextRow label="User-Agent" section="tls" field="user_agent" value={cfg.tls.user_agent} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="Mozilla/5.0 ..." />
            <CfgTextRow label="Accept-Language" section="tls" field="accept_language" value={cfg.tls.accept_language} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="en-US,en;q=0.9" />
          </div>
        </div>
      )}

      {/* 7. 服务器配置 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">服务器配置</span>
            <span className="card-hint">uvicorn 监听 · 并发 · 链路模式</span>
          </div>
          <div className="card-body">
            <CfgTextRow label="监听 Host" section="server" field="host" value={cfg.server.host} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="0.0.0.0" />
            <div className="setting-row">
              <span className="setting-label">监听端口</span>
              <div className="setting-control">
                <input className="input" type="number" value={cfg.server.port} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, port: v } } : c); scheduleCfgSave("server", { port: v }); }} style={{ width: 100 }} />
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">最大并发链路</span>
              <div className="setting-control">
                <input className="input" type="number" value={cfg.server.max_concurrent_chains} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, max_concurrent_chains: v } } : c); scheduleCfgSave("server", { max_concurrent_chains: v }); }} style={{ width: 100 }} />
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">线程池大小</span>
              <div className="setting-control">
                <input className="input" type="number" value={cfg.server.thread_pool_size} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, thread_pool_size: v } } : c); scheduleCfgSave("server", { thread_pool_size: v }); }} style={{ width: 100 }} />
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">链路模式</span>
              <div className="setting-control">
                <select className="select" value={cfg.server.chain_mode} onChange={(e) => { const v = e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, chain_mode: v } } : c); scheduleCfgSave("server", { chain_mode: v }); }} style={{ width: 140 }}>
                  <option value="live">live (真实)</option>
                  <option value="mock">mock (模拟)</option>
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">Mock 成功率</span>
              <div className="setting-control">
                <input className="input" type="number" step="0.1" value={cfg.server.mock_success_rate} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, mock_success_rate: v } } : c); scheduleCfgSave("server", { mock_success_rate: v }); }} style={{ width: 100 }} />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 8. MoMo 补丁 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">MoMo 补丁</span>
            <span className="card-hint">五层 Patch 开关</span>
          </div>
          <div className="card-body">
            <MoMoToggle label="启用 MoMo" field="enabled" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L1 拦截 CONNECT" field="connect_intercept" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L2 Clash fake-ip 重解析" field="dns_fix" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L3 payment_method 注入" field="pm_inject" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L4 confirm payload 构造" field="confirm_build" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L5 MoMo 支付 URL 正则" field="resolve_regex" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
          </div>
        </div>
      )}

      {/* 9. 存储 / 日志 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">存储 / 日志</span>
            <span className="card-hint">SQLite · 样本目录 · 日志级别</span>
          </div>
          <div className="card-body">
            <CfgTextRow label="Token 数据库" section="storage" field="db_path" value={cfg.storage.db_path} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="tokens.db" />
            <CfgTextRow label="样本目录" section="storage" field="samples_dir" value={cfg.storage.samples_dir} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="samples" />
            <CfgTextRow label="运行目录" section="storage" field="runs_dir" value={cfg.storage.runs_dir} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="runs" />
            <div className="setting-row">
              <span className="setting-label">日志级别</span>
              <div className="setting-control">
                <select className="select" value={cfg.logging.level} onChange={(e) => { const v = e.target.value; setCfg(c => c ? { ...c, logging: { ...c.logging, level: v } } : c); scheduleCfgSave("logging", { level: v }); }} style={{ width: 140 }}>
                  <option value="DEBUG">DEBUG</option>
                  <option value="INFO">INFO</option>
                  <option value="WARNING">WARNING</option>
                  <option value="ERROR">ERROR</option>
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">JSON 日志</span>
              <div className="setting-control">
                <label className="switch">
                  <input type="checkbox" checked={cfg.logging.json_logs} onChange={(e) => { const v = e.target.checked; setCfg(c => c ? { ...c, logging: { ...c.logging, json_logs: v } } : c); scheduleCfgSave("logging", { json_logs: v }); }} />
                  <span className="switch-track" />
                </label>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 10. IP 地理查询 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">IP 地理查询</span>
            <span className="card-hint">出口国探测 · 数据源 · 超时</span>
          </div>
          <div className="card-body">
            <div className="setting-row">
              <span className="setting-label">启用</span>
              <div className="setting-control">
                <label className="switch">
                  <input type="checkbox" checked={cfg.geo.enabled} onChange={(e) => { const v = e.target.checked; setCfg(c => c ? { ...c, geo: { ...c.geo, enabled: v } } : c); scheduleCfgSave("geo", { enabled: v }); }} />
                  <span className="switch-track" />
                </label>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">查询超时</span>
              <div className="setting-control">
                <input className="input" type="number" value={cfg.geo.timeout} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, geo: { ...c.geo, timeout: v } } : c); scheduleCfgSave("geo", { timeout: v }); }} style={{ width: 100 }} /> <span className="muted" style={{ fontSize: 11.5 }}>秒</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">数据源</span>
              <div className="setting-control">
                <input className="input" value={(cfg.geo.sources || []).join(", ")} onChange={(e) => { const v = e.target.value.split(",").map((s) => s.trim()).filter(Boolean); setCfg(c => c ? { ...c, geo: { ...c.geo, sources: v } } : c); scheduleCfgSave("geo", { sources: v }); }} placeholder="ip-api, ipwhois, ipinfo" style={{ width: 280 }} />
                <span className="setting-hint">逗号分隔</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 子组件 ──────────────────────────────────────────────────────── */

// secrets.json 单字段文本/密码行
function SecretRow({ label, value, onChange, placeholder, password, hint }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; password?: boolean; hint?: string;
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control">
        <input className="input" type={password ? "password" : "text"} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} style={{ width: 300 }} />
        {hint && <span className="setting-hint">{hint}</span>}
      </div>
    </div>
  );
}

// secrets.json 下拉行
function SecretSelectRow({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: [string, string][];
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control">
        <select className="select" value={value} onChange={(e) => onChange(e.target.value)} style={{ width: 220 }}>
          <option value="">(默认)</option>
          {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
        </select>
      </div>
    </div>
  );
}

// QG 代理池凭据行 (host/port/auth_key/auth_pwd)
function PoolRow({ label, pool, onChange }: {
  label: string; pool?: ProxyPool; onChange: (p: ProxyPool) => void;
}) {
  const p = pool || { host: "", port: 0, auth_key: "", auth_pwd: "" };
  const upd = (k: keyof ProxyPool, v: string | number) => onChange({ ...p, [k]: v });
  return (
    <>
      <div className="setting-row">
        <span className="setting-label">{label} host</span>
        <div className="setting-control">
          <input className="input" value={p.host} onChange={(e) => upd("host", e.target.value)} placeholder="proxy.qg.example.com" style={{ width: 200 }} />
          <input className="input" type="number" value={p.port || ""} onChange={(e) => upd("port", +e.target.value)} placeholder="端口" style={{ width: 80 }} />
        </div>
      </div>
      <div className="setting-row">
        <span className="setting-label">{label} 凭据</span>
        <div className="setting-control">
          <input className="input" value={p.auth_key} onChange={(e) => upd("auth_key", e.target.value)} placeholder="auth_key" style={{ width: 180 }} />
          <input className="input" type="password" value={p.auth_pwd} onChange={(e) => upd("auth_pwd", e.target.value)} placeholder="auth_pwd" style={{ width: 180 }} />
        </div>
      </div>
    </>
  );
}

// config.yaml A 层文本行 (通用)
function CfgTextRow({ label, section, field, value, onCfg, scheduleSave, placeholder }: {
  label: string; section: ConfigSection; field: string; value: string; onCfg: React.Dispatch<React.SetStateAction<ConfigScalars | null>>; scheduleSave: (s: ConfigSection, f: Record<string, unknown>) => void; placeholder?: string;
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control">
        <input className="input" value={value} placeholder={placeholder} onChange={(e) => {
          const v = e.target.value;
          onCfg((c) => {
            if (!c) return c;
            if (section === "stripe") return { ...c, stripe: { ...c.stripe, [field]: v } };
            if (section === "tls") return { ...c, tls: { ...c.tls, [field]: v } };
            if (section === "storage") return { ...c, storage: { ...c.storage, [field]: v } };
            return c;
          });
          scheduleSave(section, { [field]: v });
        }} style={{ width: 320 }} />
      </div>
    </div>
  );
}

// MoMo bool 开关行
function MoMoToggle({ label, field, cfg, setCfg, scheduleSave }: {
  label: string; field: keyof ConfigScalars["momo"]; cfg: ConfigScalars; setCfg: React.Dispatch<React.SetStateAction<ConfigScalars | null>>; scheduleSave: (s: ConfigSection, f: Record<string, unknown>) => void;
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control">
        <label className="switch">
          <input type="checkbox" checked={cfg.momo[field]} onChange={(e) => { const v = e.target.checked; setCfg((c) => c ? { ...c, momo: { ...c.momo, [field]: v } } : c); scheduleSave("momo", { [field]: v }); }} />
          <span className="switch-track" />
        </label>
      </div>
    </div>
  );
}

// 邮箱域名池卡片 (PayPal 注册邮箱域名, 按国家可配置, 不再硬编码)
function EmailDomainsCard() {
  const [byCountry, setByCountry] = useState<Record<string, string[]>>({});
  const [fallback, setFallback] = useState<string[]>([]);
  const [country, setCountry] = useState("US");
  const [savedFlash, setSavedFlash] = useState("");
  const [loading, setLoading] = useState(true);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; by_country?: Record<string, string[]>; fallback?: string[] }>("/api/config/email_domains", "GET");
      if (r?.ok) {
        setByCountry(r.by_country || {});
        setFallback(r.fallback || []);
        const keys = Object.keys(r.by_country || {});
        if (keys.length && !keys.includes(country)) setCountry(keys[0]);
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [country]);

  useEffect(() => { load(); }, [load]);

  const scheduleSave = (nextBy: Record<string, string[]>, nextFallback: string[]) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      try {
        await api("/api/config/email_domains", "POST", { by_country: nextBy, fallback: nextFallback });
        setSavedFlash("已保存 ✓");
        setTimeout(() => setSavedFlash(""), 1500);
      } catch { /* ignore */ }
    }, 800);
  };

  const updCountry = (val: string) => {
    const domains = val.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    const next = { ...byCountry, [country]: domains };
    setByCountry(next);
    scheduleSave(next, fallback);
  };
  const updFallback = (val: string) => {
    const domains = val.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    setFallback(domains);
    scheduleSave(byCountry, domains);
  };
  const reset = async () => {
    if (!window.confirm("重置为内置默认域名池？用户自定义将丢失。")) return;
    try {
      const r = await api<{ ok: boolean; by_country?: Record<string, string[]>; fallback?: string[] }>("/api/config/email_domains", "POST", { reset: true });
      if (r?.ok) {
        setByCountry(r.by_country || {});
        setFallback(r.fallback || []);
        setSavedFlash("已重置 ✓");
        setTimeout(() => setSavedFlash(""), 1500);
      }
    } catch { /* ignore */ }
  };

  const countryKeys = Object.keys(byCountry).sort();
  const currentDomains = (byCountry[country] || []).join(", ");
  const fallbackStr = fallback.join(", ");

  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">邮箱域名池</span>
        <span className="card-hint">PayPal 注册邮箱域名 · 按国家配置（留空回落内置默认）</span>
      </div>
      <div className="card-body">
        {loading ? (
          <div className="muted" style={{ fontSize: 12.5 }}>加载中…</div>
        ) : (
          <>
            <div className="setting-row">
              <span className="setting-label">国家</span>
              <div className="setting-control">
                <select className="select" value={country} onChange={(e) => setCountry(e.target.value)} style={{ width: 120 }}>
                  {countryKeys.map((c) => (<option key={c} value={c}>{c}</option>))}
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">{country} 域名</span>
              <div className="setting-control">
                <input className="input" value={currentDomains} onChange={(e) => updCountry(e.target.value)} placeholder="例: gmail.com, outlook.com" style={{ width: 320 }} />
                <span className="muted" style={{ fontSize: 11.5 }}>逗号分隔</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">通用 fallback</span>
              <div className="setting-control">
                <input className="input" value={fallbackStr} onChange={(e) => updFallback(e.target.value)} placeholder="例: gmail.com, yahoo.com" style={{ width: 320 }} />
                <span className="muted" style={{ fontSize: 11.5 }}>无国家匹配时使用 · 逗号分隔</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label"></span>
              <div className="setting-control">
                <button className="btn btn-ghost" onClick={reset} style={{ fontSize: 12.5 }}>↺ 重置为默认</button>
                {savedFlash && <span className="muted" style={{ fontSize: 11.5, color: "var(--ok)", marginLeft: 8 }}>{savedFlash}</span>}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

