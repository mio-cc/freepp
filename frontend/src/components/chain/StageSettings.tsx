import { useEffect, useState } from "react";
import { STAGE_ORDER, STAGE_SHORT, STAGE_CN, BRANCH_CN } from "../../types";
import type { StageName, BranchName, StageCfg, BranchCfg } from "../../types";

/* ==========================================================================
   提链链路页共享组件: 国家下拉单选(auto+搜索) / 分支开关 / 段配置行 / 七段面板
   (PayPal 提炼 / MoMo 提链 / Grok 链路 等链路页通用)
   ========================================================================== */

export const flag = (cc: string): string => {
  if (!cc || cc.length !== 2) return "";
  const A = 0x1f1e6, Z = 0x1f1ff;
  const c = cc.toUpperCase().charCodeAt(0) - 65;
  const c2 = cc.toUpperCase().charCodeAt(1) - 65;
  if (c < 0 || c > 25 || c2 < 0 || c2 > 25) return "";
  return String.fromCodePoint(A + c, A + c2);
};

/* 全量候选国家 (ISO 3166 大写码, 历史 checkout_auto_countries 池) */
export const ALL_COUNTRY_CODES: string[] = `AD,AE,AF,AG,AI,AL,AM,AO,AR,AS,AT,AU,AW,AZ,BA,BB,BD,BE,BF,BG,BH,BI,BJ,BL,BM,BN,BO,BR,BS,BT,BW,BY,BZ,CA,CD,CF,CG,CH,CI,CK,CL,CM,CO,CR,CU,CV,CW,CY,CZ,DE,DJ,DK,DM,DO,DZ,EC,EE,EG,ER,ES,ET,EU,FI,FJ,FO,FR,GA,GB,GD,GE,GF,GH,GI,GL,GM,GN,GP,GQ,GR,GT,GU,GW,GY,HK,HN,HR,HT,HU,ID,IE,IL,IN,IQ,IR,IS,IT,JM,JO,JP,KE,KG,KH,KI,KM,KN,KR,KW,KY,KZ,LA,LB,LC,LI,LK,LR,LS,LT,LU,LV,LY,MA,MC,MD,ME,MG,MK,ML,MM,MN,MO,MQ,MR,MS,MT,MU,MV,MW,MX,MY,MZ,NA,NC,NE,NG,NI,NL,NO,NP,NZ,OM,PA,PE,PF,PG,PH,PK,PL,PM,PR,PT,PW,PY,QA,RE,RO,RS,RU,RW,SA,SB,SC,SD,SE,SG,SI,SK,SL,SM,SN,SO,SR,ST,SV,SX,SY,SZ,TC,TD,TG,TH,TJ,TL,TM,TN,TO,TR,TT,TW,TZ,UA,UG,US,UY,UZ,VC,VE,VG,VI,VN,VU,WS,YE,YT,ZA,ZM,ZW`.split(",");

const AUTO = "auto";

/* --------------------------------------------------------------------------
   国家下拉单选: AUTO(自动轮换) 固定在顶部 + 全量国家池搜索选择
   - 输入框实时过滤 (大小写兼容, 输入 us 筛出 US)
   - 单选 radio 风格; 选中值存 ["auto"] 或 ["US"] 数组
   -------------------------------------------------------------------------- */
export function CountrySelect({
  value,
  options,
  onChange,
  blocked = [],
  autoLabel = "AUTO · 自动轮换",
}: {
  value: string[];
  options: { code: string; capital?: string }[];
  onChange: (v: string[]) => void;
  blocked?: string[];
  autoLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const sel = value && value.length > 0 ? value[0] : AUTO;
  const display = sel === AUTO ? { code: AUTO, label: autoLabel } : { code: sel, label: `${flag(sel)} ${sel}` };

  const pool = options.length > 0 ? options.map((o) => o.code) : ALL_COUNTRY_CODES;
  const q = query.trim().toUpperCase();
  const filtered = pool.filter((c) => {
    if (c === sel) return false;
    if (blocked.includes(c)) return false;
    if (!q) return true;
    return c.includes(q) || q === c;
  });

  const choose = (code: string) => {
    onChange(code === AUTO ? [AUTO] : [code]);
    setOpen(false);
    setQuery("");
  };

  return (
    <div style={{ position: "relative", flex: 1, minWidth: 200 }}>
      <button
        className="btn btn-sm"
        style={{
          width: "100%",
          textAlign: "left",
          justifyContent: "space-between",
          gap: 8,
          minHeight: 32,
        }}
        onClick={() => setOpen(!open)}
        type="button"
      >
        <span
          style={{
            fontSize: 12,
            color: sel === AUTO ? "var(--accent-strong)" : "inherit",
            fontWeight: sel === AUTO ? 600 : 400,
          }}
        >
          {sel === AUTO ? autoLabel : display.label}
        </span>
        <span style={{ opacity: 0.6, fontSize: 10 }}>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            zIndex: 50,
            marginTop: 4,
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            boxShadow: "0 10px 32px rgba(0,0,0,.35)",
            padding: 8,
          }}
        >
          <input
            className="input"
            type="text"
            placeholder="搜索国家… 输入 us 筛出 US"
            value={query}
            autoFocus
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: "100%", marginBottom: 6 }}
          />
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 8px",
                borderRadius: 6,
                fontSize: 12,
                cursor: "pointer",
                background: sel === AUTO ? "var(--accent-dim)" : "transparent",
              }}
            >
              <input
                type="radio"
                checked={sel === AUTO}
                onChange={() => choose(AUTO)}
              />
              <span style={{ fontWeight: 600 }}>AUTO · 自动轮换</span>
              <span className="muted" style={{ fontSize: 10 }}>全量国家池动态优选</span>
            </label>
            {filtered.map((c) => (
              <label
                key={c}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "5px 8px",
                  borderRadius: 6,
                  fontSize: 12,
                  cursor: "pointer",
                  background: sel === c ? "var(--accent-dim)" : "transparent",
                }}
              >
                <input type="radio" checked={sel === c} onChange={() => choose(c)} />
                <span>{flag(c)} {c}</span>
                {q && <span className="muted" style={{ fontSize: 10 }}>✓ 匹配</span>}
              </label>
            ))}
            {filtered.length === 0 && (
              <div className="muted" style={{ fontSize: 12, padding: 6 }}>
                无匹配国家
              </div>
            )}
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              borderTop: "1px solid var(--border-faint)",
              marginTop: 6,
              paddingTop: 6,
            }}
          >
            <button className="btn btn-primary btn-sm" type="button" onClick={() => setOpen(false)}>
              完成
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------------
   分支开关行
   -------------------------------------------------------------------------- */
export function BranchToggle({
  label,
  desc,
  value,
  onChange,
}: {
  label: string;
  desc: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control" style={{ gap: 10 }}>
        <label className="switch">
          <input
            type="checkbox"
            checked={value}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span className="switch-track" />
        </label>
        <span className="muted" style={{ fontSize: 11.5 }}>{desc}</span>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   段配置行 (国家下拉单选 + 超时/重试 —— 点击字段即编辑, 改动自动保存)
   -------------------------------------------------------------------------- */
export function StageRow({
  stage,
  cfg,
  countries,
  onSave,
  saving,
}: {
  stage: StageName;
  cfg: StageCfg;
  countries: { code: string; capital?: string }[];
  onSave: (stage: StageName, patch: Partial<StageCfg>) => void;
  saving: boolean;
}) {
  const [sel, setSel] = useState<string[]>(cfg.countries || []);
  const [timeout, setTimeout] = useState(String(cfg.timeout));
  const [retry, setRetry] = useState(String(cfg.retry));
  const [pollInterval, setPollInterval] = useState(String(cfg.poll_interval ?? ""));
  const [maxPolls, setMaxPolls] = useState(String(cfg.max_polls ?? ""));
  const [active, setActive] = useState<"timeout" | "retry" | "poll_interval" | "max_polls" | null>(null);

  useEffect(() => {
    setSel(cfg.countries || []);
    setTimeout(String(cfg.timeout));
    setRetry(String(cfg.retry));
    setPollInterval(String(cfg.poll_interval ?? ""));
    setMaxPolls(String(cfg.max_polls ?? ""));
  }, [cfg]);

  const isPoll = stage === "poll";

  const commit = (patch: Partial<StageCfg>) => {
    onSave(stage, patch);
    setActive(null);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "11px 0",
        borderBottom: "1px solid var(--border-faint)",
        flexWrap: "wrap",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, width: 130, flexShrink: 0 }}>
        <span
          className="tag"
          style={{
            color: "var(--accent-strong)",
            background: "var(--accent-dim)",
            border: "1px solid rgba(108,108,248,.3)",
          }}
        >
          {STAGE_SHORT[stage]}
        </span>
        <span style={{ fontWeight: 600, fontSize: 12.5 }}>{STAGE_CN[stage]}</span>
        <span className="muted" style={{ fontSize: 10.5, fontFamily: "var(--font-mono)" }}>
          {stage}
        </span>
      </div>

      <div style={{ flex: 1, minWidth: 220 }}>
        <CountrySelect
          value={sel}
          options={countries}
          onChange={(v) => {
            setSel(v);
            onSave(stage, { countries: v });
          }}
        />
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
        <div className="inline-field">
          <label>超时(s)</label>
          <input
            className="input"
            type="number"
            style={active === "timeout" ? undefined : { width: 70, borderColor: "transparent", background: "var(--bg-raised)", cursor: "pointer" }}
            value={timeout}
            onFocus={() => setActive("timeout")}
            onBlur={() => commit({ timeout: parseInt(timeout) || 10 })}
            onChange={(e) => setTimeout(e.target.value)}
          />
        </div>
        <div className="inline-field">
          <label>重试</label>
          <input
            className="input"
            type="number"
            style={active === "retry" ? undefined : { width: 70, borderColor: "transparent", background: "var(--bg-raised)", cursor: "pointer" }}
            value={retry}
            onFocus={() => setActive("retry")}
            onBlur={() => commit({ retry: parseInt(retry) || 3 })}
            onChange={(e) => setRetry(e.target.value)}
          />
        </div>
        {isPoll && (
          <>
            <div className="inline-field">
              <label>轮询间隔(s)</label>
              <input
                className="input"
                type="number"
                step="0.01"
                style={active === "poll_interval" ? undefined : { width: 70, borderColor: "transparent", background: "var(--bg-raised)", cursor: "pointer" }}
                value={pollInterval}
                onFocus={() => setActive("poll_interval")}
                onBlur={() => commit({ poll_interval: parseFloat(pollInterval) || 0.75 })}
                onChange={(e) => setPollInterval(e.target.value)}
              />
            </div>
            <div className="inline-field">
              <label>最大轮次</label>
              <input
                className="input"
                type="number"
                style={active === "max_polls" ? undefined : { width: 70, borderColor: "transparent", background: "var(--bg-raised)", cursor: "pointer" }}
                value={maxPolls}
                onFocus={() => setActive("max_polls")}
                onBlur={() => commit({ max_polls: parseInt(maxPolls) || 40 })}
                onChange={(e) => setMaxPolls(e.target.value)}
              />
            </div>
          </>
        )}
        {saving && <span className="muted" style={{ fontSize: 11 }}>保存中…</span>}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   七段管道设置面板 (提链链路页通用: 开关 + 双init出口 + 七段 + 流程条)
   -------------------------------------------------------------------------- */
export function StageSettingsPanel({
  branchName,
  branch,
  countries,
  blocked,
  onSaveStage,
  onSaveFlags,
  savingStage,
  savingFlags,
}: {
  branchName: BranchName;
  branch: BranchCfg;
  countries: { code: string; capital?: string }[];
  blocked?: string[];
  onSaveStage: (stage: StageName, patch: Partial<StageCfg>) => void;
  onSaveFlags: (patch: Partial<BranchCfg>) => void;
  savingStage: string;
  savingFlags: boolean;
}) {
  const stages = branch.stages || {};
  const chanLabel: Record<string, string> = {
    paypal: "PayPal 渠道",
    momo: "MoMo 渠道",
    card: "卡片渠道",
    link: "链接渠道",
    pix: "PIX 渠道",
    ideal: "iDEAL 渠道",
    upi: "UPI 渠道",
    kakao: "Kakao Pay 渠道",
    blik: "BLIK 渠道",
    twint: "TWINT 渠道",
  };

  return (
    <div className="card settings-panel">
      <div className="card-head">
        <span className="card-title">
          {BRANCH_CN[branchName]} · 七段管道
        </span>
        <span className="card-hint">
          渠道校验: {chanLabel[branch.channel] || branch.channel} · token 库: {branch.token_source || branchName}
          {savingFlags && <span style={{ marginLeft: 8, color: "var(--accent-strong)" }}>保存中…</span>}
        </span>
      </div>
      <div className="card-body">
        <BranchToggle
          label="双 Init"
          desc="init0 借道出口拿渠道类型 → init1 回本地验真 → init_t 过渡"
          value={!!branch.dual_init}
          onChange={(v) => onSaveFlags({ dual_init: v })}
        />
        <BranchToggle
          label="支付渠道校验"
          desc={`init 返回的 payment_method_types 须含 ${branch.channel || "paypal"}`}
          value={!!branch.channel_check}
          onChange={(v) => onSaveFlags({ channel_check: v })}
        />
        <BranchToggle
          label="金额校验"
          desc="init.invoice.amount_due 须为 0 (fail-closed)"
          value={!!branch.require_zero}
          onChange={(v) => onSaveFlags({ require_zero: v })}
        />
        <BranchToggle
          label="分段跟随"
          desc="除 update 段外，其余段出口国跟随 checkout 段"
          value={!!branch.follow_checkout}
          onChange={(v) => onSaveFlags({ follow_checkout: v })}
        />
      </div>

      {branch.dual_init && (
        <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
          <div className="section-head">
            <span className="section-title">双 Init 出口（init0 → init1 → init_t）</span>
          </div>
          {(
            [
              ["init0_ccs", "init0 · 借道出口", "拿 payment_method_types"],
              ["init1_ccs", "init1 · 验真出口", "本地验真"],
              ["init_t_ccs", "init_t · 过渡出口", "过渡"],
            ] as const
          ).map(([key, label, desc]) => (
            <div className="setting-row" key={key}>
              <span className="setting-label">{label}</span>
              <div className="setting-control" style={{ flex: 1, gap: 10 }}>
                <CountrySelect
                  value={(branch[key] as string[]) || []}
                  options={countries}
                  onChange={(v) => onSaveFlags({ [key]: v })}
                />
                <span className="muted" style={{ fontSize: 11.5, width: 90, flexShrink: 0 }}>{desc}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
        <div className="section-head">
          <span className="section-title">账单国</span>
          <span className="muted" style={{ fontSize: 11.5 }}>provider 段出账单地址的国家 · 改动自动保存</span>
        </div>
        <div className="setting-row">
          <span className="setting-label">账单国</span>
          <div className="setting-control" style={{ flex: 1, gap: 10 }}>
            <CountrySelect
              value={[branch.billing_country || "auto"]}
              options={countries}
              autoLabel="AUTO · 跟随 checkout 段"
              onChange={(v) => onSaveFlags({ billing_country: (v[0] || "auto") === "auto" ? "auto" : v[0] })}
            />
            <span className="muted" style={{ fontSize: 11.5, width: 90, flexShrink: 0 }}>
              {branch.billing_country && branch.billing_country !== "auto"
                ? "固定账单国"
                : "跟随 checkout 段"}
            </span>
          </div>
        </div>
        <div className="setting-row">
          <span className="setting-label">总尝试</span>
          <div className="setting-control" style={{ flex: 1, gap: 10 }}>
            <input
              className="input"
              type="number"
              min={1}
              value={branch.attempts || 8}
              onChange={(e) => {
                const v = Math.max(1, +e.target.value);
                onSaveFlags({ attempts: v });
              }}
              style={{ width: 140 }}
            />
            <span className="muted" style={{ fontSize: 11.5, width: 90, flexShrink: 0 }}>
              每 Token 最大尝试轮数
            </span>
          </div>
        </div>
      </div>

      <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
        {STAGE_ORDER.map((stage) => {
          const sc = (stages[stage] as StageCfg) || { countries: [], timeout: 10, retry: 3 };
          return (
            <StageRow
              key={stage}
              stage={stage}
              cfg={sc}
              countries={countries}
              onSave={(st, patch) => onSaveStage(st, patch)}
              saving={savingStage === stage}
            />
          );
        })}
      </div>
      <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
        <div className="flow-chain" style={{ borderBottom: "none", padding: "2px 0 0" }}>
          {STAGE_ORDER.map((stage, i) => {
            const sc = stages[stage];
            const cc = (sc as StageCfg)?.countries?.[0] || "—";
            const label = cc === "auto" ? "AUTO" : `${flag(cc)}${cc}`;
            return (
              <span key={stage} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <span className="flow-node">
                  {STAGE_SHORT[stage]} {label}
                </span>
                {i < STAGE_ORDER.length - 1 && <span className="flow-arrow">→</span>}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}
