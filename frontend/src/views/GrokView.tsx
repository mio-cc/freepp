import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { StageName, StageCfg, BranchCfg } from "../types";
import { StageSettingsPanel } from "../components/chain/StageSettings";

/* ==========================================================================
   Grok 链路 — 配置模块 (七段出口配置, grok 分支 = card 渠道)
   启动在 Token 库 (分支: Grok 链路) · 产出在成功库存 · 进度在链路监控
   ========================================================================== */

export function GrokView() {
  const [branch, setBranch] = useState<BranchCfg | null>(null);
  const [countryOptions, setCountryOptions] = useState<{ code: string; capital?: string }[]>([]);
  const [savingStage, setSavingStage] = useState<string>("");
  const [savingFlags, setSavingFlags] = useState(false);
  const [result, setResult] = useState("");

  const loadBranch = useCallback(async () => {
    try {
      const data = await api("/api/config");
      if (data && data.ok && data.chain?.branches?.grok) {
        setBranch(data.chain.branches.grok);
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
      await api("/api/config/branch", "POST", { branch: "grok", stages: { [stage]: patch } });
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
      await api("/api/config/branch", "POST", { branch: "grok", ...patch });
      setBranch((prev) => (prev ? { ...prev, ...patch } : prev));
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
          <h2 className="page-title">Grok 链路</h2>
          <p className="page-sub">
            七段出口配置 (card 渠道) — 启动在 Token 库
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
          branchName="grok"
          branch={branch}
          countries={countryOptions}
          onSaveStage={handleSaveStage}
          onSaveFlags={handleSaveFlags}
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
        本页只负责 <b>Grok 链路出口配置</b>：选择 Token 并批量提链请到 <b>Token 库</b>
        （提链分支选「Grok 链路」）；链路进度看<b>链路监控</b>；产出看<b>成功库存</b>。
      </div>
    </div>
  );
}

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
    name: "grok",
    label: "Grok 链路",
    channel: "card",
    token_source: "grok",
    require_zero: false,
    channel_check: true,
    dual_init: false,
    init0_ccs: ["auto"],
    init1_ccs: ["US"],
    init_t_ccs: ["auto"],
    follow_checkout: true,
    billing_country: "auto",
    attempts: 8,
    stages: mkStages([]),
  };
}
