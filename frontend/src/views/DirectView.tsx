import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { StageName, StageCfg, BranchCfg } from "../types";
import { CountrySelect, StageRow } from "../components/chain/StageSettings";

/* ==========================================================================
   直卡提链 — 精简配置页 (仅 2 段: checkout → update 压 0)
   pay.153 ph_short 模式: 代理池 1=US 创建 PH/PHP Checkout, 代理池 2=TR 应用优惠
   ========================================================================== */

const DIRECT_STAGES: StageName[] = ["checkout", "update"];

export function DirectView() {
  const [branch, setBranch] = useState<BranchCfg | null>(null);
  const [countryOptions, setCountryOptions] = useState<{ code: string; capital?: string }[]>([]);
  const [savingStage, setSavingStage] = useState<string>("");
  const [savingFlags, setSavingFlags] = useState(false);
  const [result, setResult] = useState("");

  const loadBranch = useCallback(async () => {
    try {
      const data = await api("/api/config");
      if (data && data.ok && data.chain?.branches?.direct) {
        setBranch(data.chain.branches.direct);
        setResult("");
      } else {
        setBranch(makeMockBranch());
        setResult("后端离线，展示默认配置");
      }
    } catch {
      setBranch(makeMockBranch());
      setResult("后端离线，展示默认配置");
    }
  }, []);

  const loadCountries = useCallback(async () => {
    try {
      const data = await api("/api/billing/templates");
      if (data && data.ok && Array.isArray(data.templates)) {
        setCountryOptions(
          data.templates.map((t: any) => ({
            code: t.country,
            capital: `${t.city} · ${t.currency}`,
          }))
        );
      }
    } catch {
      setCountryOptions([]);
    }
  }, []);

  useEffect(() => {
    loadBranch();
    loadCountries();
  }, [loadBranch, loadCountries]);

  const handleSaveStage = async (stage: StageName, patch: Partial<StageCfg>) => {
    setSavingStage(stage);
    try {
      await api("/api/config/branch", "POST", { branch: "direct", stages: { [stage]: patch } });
      setBranch((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          stages: { ...prev.stages, [stage]: { ...(prev.stages[stage] as StageCfg), ...patch } as StageCfg },
        };
      });
    } catch {
      // 静默
    } finally {
      setSavingStage("");
    }
  };

  const handleSaveFlags = async (patch: Partial<BranchCfg>) => {
    setSavingFlags(true);
    try {
      await api("/api/config/branch", "POST", { branch: "direct", ...patch });
      setBranch((prev) => (prev ? { ...prev, ...patch } : prev));
    } catch {
      // 静默
    } finally {
      setSavingFlags(false);
    }
  };

  const stages = branch?.stages || {};

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">直卡提链</h2>
          <p className="page-sub">
            checkout(US 出口 / PH 账单) → update(TR 出口压 0) → 产出 checkout 短链
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" onClick={loadBranch}>
            刷新配置
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">直卡 · 两段管道</span>
          <span className="card-hint">
            渠道: card · token 库: direct
            {savingFlags && <span style={{ marginLeft: 8, color: "var(--accent-strong)" }}>保存中…</span>}
          </span>
        </div>
        <div className="card-body">
          <div className="setting-row">
            <span className="setting-label">账单国</span>
            <div className="setting-control" style={{ flex: 1, gap: 10 }}>
              <CountrySelect
                value={[branch?.billing_country || "PH"]}
                options={countryOptions}
                autoLabel="AUTO · 跟随 checkout 段"
                onChange={(v) => onSaveFlagsWrapper(v, handleSaveFlags)}
              />
              <span className="muted" style={{ fontSize: 11.5, width: 90, flexShrink: 0 }}>
                固定账单国
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
                value={branch?.attempts || 8}
                onChange={(e) => handleSaveFlags({ attempts: Math.max(1, +e.target.value) })}
                style={{ width: 140 }}
              />
              <span className="muted" style={{ fontSize: 11.5, width: 90, flexShrink: 0 }}>
                每 Token 最大尝试轮数
              </span>
            </div>
          </div>
        </div>

        <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
          {DIRECT_STAGES.map((stage) => {
            const sc = (stages[stage] as StageCfg) || { countries: [], timeout: 45, retry: 3 };
            return (
              <StageRow
                key={stage}
                stage={stage}
                cfg={sc}
                countries={countryOptions}
                onSave={(st, patch) => handleSaveStage(st as StageName, patch)}
                saving={savingStage === stage}
              />
            );
          })}
        </div>
        <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
          <div className="flow-chain" style={{ borderBottom: "none", padding: "2px 0 0" }}>
            {DIRECT_STAGES.map((stage, i) => {
              const sc = stages[stage];
              const cc = (sc as StageCfg)?.countries?.[0] || "—";
              const label = cc === "auto" ? "AUTO" : cc;
              return (
                <span key={stage} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                  <span className="flow-node">
                    {stage.toUpperCase()} {label}
                  </span>
                  {i < DIRECT_STAGES.length - 1 && <span className="flow-arrow">→</span>}
                </span>
              );
            })}
            <span className="flow-arrow">→</span>
            <span className="flow-node accent">短链</span>
          </div>
        </div>
      </div>

      <div className="note" style={{ marginTop: 14 }}>
        <b>pay.153 配方</b>：代理池 1 使用 <b>US</b> 创建 PH/PHP Checkout，代理池 2 使用{" "}
        <b>TR</b> 应用优惠（压 0）。产出短链：<code>chatgpt.com/checkout/openai_llc/oaics_…</code>
      </div>

      {result && (
        <div className="note" style={{ marginTop: 14 }}>
          {result}
        </div>
      )}
    </div>
  );
}

async function onSaveFlagsWrapper(
  v: string[],
  save: (patch: Partial<BranchCfg>) => void | Promise<void>
) {
  // save 自行管理 saving 指示 (async), 此处仅需透传补丁
  await save({ billing_country: (v[0] || "auto") === "auto" ? "auto" : v[0] });
}

function makeMockBranch(): BranchCfg {
  const mkStages = (): Partial<Record<StageName, StageCfg>> => ({
    checkout: { countries: ["US"], timeout: 45, retry: 3 },
    update: { countries: ["TR"], timeout: 45, retry: 3 },
  });
  return {
    name: "direct",
    label: "直卡提链",
    channel: "card",
    token_source: "direct",
    require_zero: true,
    channel_check: false,
    dual_init: false,
    init0_ccs: [],
    init1_ccs: [],
    init_t_ccs: [],
    follow_checkout: false,
    billing_country: "PH",
    attempts: 8,
    stages: mkStages(),
  };
}
