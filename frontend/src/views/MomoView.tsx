import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { StageName, StageCfg, BranchCfg, BranchName } from "../types";
import { StageSettingsPanel } from "../components/chain/StageSettings";

/* ==========================================================================
   MoMo 提链 — 提链配置模块 (七段出口配置)
   提链启动在 Token 库 (分支: momo) · 产出在成功库存 · 进度在链路监控
   ========================================================================== */

export function MomoView() {
  /* ── 七段出口 (momo 分支) ── */
  const [branch, setBranch] = useState<BranchCfg | null>(null);
  const [countryOptions, setCountryOptions] = useState<{ code: string; capital?: string }[]>([]);
  const [savingStage, setSavingStage] = useState<string>("");
  const [savingFlags, setSavingFlags] = useState(false);
  const [result, setResult] = useState("");

  const loadBranch = useCallback(async () => {
    try {
      const data = await api("/api/config");
      if (data && data.ok && data.chain?.branches?.momo) {
        setBranch(data.chain.branches.momo);
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
      await api("/api/config/branch", "POST", { branch: "momo", stages: { [stage]: patch } });
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
      await api("/api/config/branch", "POST", { branch: "momo", ...patch });
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
          <h2 className="page-title">MoMo 提链</h2>
          <p className="page-sub">
            七段出口配置 · 渠道校验 (momo) · 分段跟随 · 账单国 — 启动在 Token 库
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
          branchName="momo"
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
        本页只负责 <b>MoMo 提链链路的出口配置</b>：选择 Token 并批量提链请到{" "}
        <b>Token 库</b>（提链分支选「MoMo 提链」）；链路进度看<b>链路监控</b>；
        产出支付 URL 看<b>成功库存</b>。
      </div>
    </div>
  );
}

/* ==========================================================================
   Mock 分支 (后端离线时用于渲染七段面板)
   ========================================================================== */
function makeMockBranch(): BranchCfg {
  const mkStages = (cc: string[]): Partial<Record<StageName, StageCfg>> => ({
    checkout: { countries: cc.length ? cc : ["VN"], timeout: 15, retry: 3 },
    init: { countries: ["VN"], timeout: 10, retry: 3 },
    update: { countries: ["VN"], timeout: 10, retry: 3 },
    provider: { countries: ["VN"], timeout: 8, retry: 3 },
    approve: { countries: ["VN"], timeout: 6, retry: 3 },
    poll: { countries: ["VN"], timeout: 25, retry: 1, poll_interval: 0.75, max_polls: 40 },
    resolve: { countries: ["VN"], timeout: 20, retry: 2 },
  });

  return {
    name: "momo",
    label: "MoMo 提链",
    channel: "momo",
    token_source: "momo",
    require_zero: true,
    channel_check: true,
    dual_init: true,
    init0_ccs: ["VN"],
    init1_ccs: ["VN"],
    init_t_ccs: ["VN"],
    follow_checkout: true,
    billing_country: "auto",
    attempts: 8,
    stages: mkStages(["VN"]),
  };
}
