import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { StageName, StageCfg, BranchCfg, OaicsStageName, OaicsBranchCfg } from "../types";
import { StageSettingsPanel } from "../components/chain/StageSettings";

/* ==========================================================================
   PayPal 提炼 — 提链配置模块 (七段出口配置)
   提链启动在 Token 库 (分支: paypal) · 产出在成功库存 · 进度在链路监控
   ========================================================================== */

export function PayPalExtractView() {
  /* ── 七段出口 (paypal 分支) ── */
  const [branch, setBranch] = useState<BranchCfg | null>(null);
  const [countryOptions, setCountryOptions] = useState<{ code: string; capital?: string }[]>([]);
  const [savingStage, setSavingStage] = useState<string>("");
  const [savingFlags, setSavingFlags] = useState(false);
  const [result, setResult] = useState("");

  const loadBranch = useCallback(async () => {
    try {
      const data = await api("/api/config");
      if (data && data.ok && data.chain?.branches?.paypal) {
        setBranch(data.chain.branches.paypal);
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
      await api("/api/config/branch", "POST", { branch: "paypal", stages: { [stage]: patch } });
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
      await api("/api/config/branch", "POST", { branch: "paypal", ...patch });
      setBranch((prev) => (prev ? { ...prev, ...patch } : prev));
    } catch {
      // 静默
    } finally {
      setSavingFlags(false);
    }
  };

  const handleSaveOaicsStage = async (stage: OaicsStageName, patch: Partial<StageCfg>) => {
    setSavingStage(stage);
    try {
      await api("/api/config/branch", "POST", {
        branch: "paypal",
        oaics: {
          billing_country: branch?.oaics?.billing_country,
          attempts: branch?.oaics?.attempts,
          stages: { [stage]: patch },
        },
      });
      setBranch((prev) => {
        if (!prev?.oaics) return prev;
        return {
          ...prev,
          oaics: {
            ...prev.oaics,
            stages: { ...prev.oaics.stages, [stage]: { ...(prev.oaics.stages[stage] as StageCfg), ...patch } as StageCfg },
          },
        };
      });
    } catch {
      // 静默
    } finally {
      setSavingStage("");
    }
  };

  const handleSaveOaicsFlags = async (patch: Partial<OaicsBranchCfg>) => {
    setSavingFlags(true);
    try {
      await api("/api/config/branch", "POST", {
        branch: "paypal",
        oaics: { ...(branch?.oaics || {}), ...patch } as any,
      });
      setBranch((prev) => (prev ? { ...prev, oaics: { ...(prev.oaics || ({} as OaicsBranchCfg)), ...patch } } : prev));
    } catch {
      // 静默
    } finally {
      setSavingFlags(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">PayPal 提炼</h2>
          <p className="page-sub">
            双链路配置 · 原七段 (cs_live / hosted) + OAICS 五段 (custom 纯 HTTP)
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" onClick={loadBranch}>
            刷新配置
          </button>
        </div>
      </div>

      {branch && (
        <StageSettingsPanel
          branchName="paypal"
          branch={branch}
          countries={countryOptions}
          onSaveStage={handleSaveStage}
          onSaveFlags={handleSaveFlags}
          onSaveOaicsStage={handleSaveOaicsStage}
          onSaveOaicsFlags={handleSaveOaicsFlags}
          savingStage={savingStage}
          savingFlags={savingFlags}
        />
      )}

      {result && (
        <div className="note" style={{ marginTop: 14 }}>
          {result}
        </div>
      )}

      <div className="note" style={{ marginTop: 14 }}>
        本页只负责 <b>PayPal 提炼链路的出口配置</b>：选择 Token 并批量提链请到{" "}
        <b>Token 库</b>（提链分支选「PayPal 提炼」）；链路进度看<b>链路监控</b>；
        产出 BA 看<b>成功库存</b>。PayPal 支付授权是独立流程，见<b>支付授权</b>页。
      </div>
    </div>
  );
}

/* ==========================================================================
   Mock 分支 (后端离线时用于渲染七段面板)
   ========================================================================== */
function makeMockBranch(): BranchCfg {
  const mkStages = (cc: string[]): Partial<Record<StageName, StageCfg>> => ({
    checkout: { countries: ["auto"], timeout: 15, retry: 3 },
    init: { countries: ["auto"], timeout: 10, retry: 3 },
    update: { countries: ["US"], timeout: 10, retry: 3 },
    provider: { countries: ["auto"], timeout: 8, retry: 3 },
    approve: { countries: ["auto"], timeout: 6, retry: 3 },
    poll: { countries: ["auto"], timeout: 25, retry: 1, poll_interval: 0.75, max_polls: 40 },
    resolve: { countries: ["auto"], timeout: 20, retry: 2 },
  });

  return {
    name: "paypal",
    label: "PayPal 提炼",
    channel: "paypal",
    token_source: "stripe",
    require_zero: true,
    channel_check: true,
    dual_init: false,
    init0_ccs: ["auto"],
    init1_ccs: ["US"],
    init_t_ccs: ["auto"],
    follow_checkout: false,
    billing_country: "auto",
    attempts: 8,
    stages: mkStages([]),
    oaics: {
      label: "OAICS 五段",
      billing_country: "auto",
      attempts: 5,
      stages: {
        checkout: { countries: ["US"], timeout: 15, retry: 3 },
        taxes: { countries: ["US"], timeout: 15, retry: 3 },
        provider: { countries: ["US"], timeout: 20, retry: 3 },
        confirm: { countries: ["US"], timeout: 20, retry: 3 },
        resolve: { countries: ["US"], timeout: 20, retry: 2 },
      },
    },
  };
}
