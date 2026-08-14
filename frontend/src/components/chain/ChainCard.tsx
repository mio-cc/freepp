import { memo } from "react";
import { STAGE_ORDER, STAGE_SHORT, STAGE_CN, OAICS_STAGE_ORDER, OAICS_STAGE_SHORT, OAICS_STAGE_CN } from "../../types";
import type { ChainState, StageData } from "../../types";

const fmtDur = (sec: number) => {
  if (sec == null || isNaN(sec)) return "—";
  if (sec < 60) return sec.toFixed(1) + "s";
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}m${s}s`;
};

const trunc = (s: string, n = 22) => {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
};

/** 段标签: 有真实国家则显示真实 + 飘移/复用标记 */
const stageLabel = (sd?: StageData) => {
  if (!sd) return "";
  const req = sd.country || "";
  const act = sd.actualCountry || "";
  let s = act || req || "";
  if (act && req && act !== req) s = `${act}⚠`;
  if (sd.reusedFrom) s += `⇄${sd.reusedFrom}`;
  return s;
};

interface Props {
  chainId: string;
  cs: ChainState;
  onClick?: (url: string, meta: string) => void;
}

function ChainCardInner({ chainId, cs, onClick }: Props) {
  // 终端状态使用后端固化的 elapsed, 运行中才实时计时
  const elapsed =
    cs.elapsed ??
    (cs.status === "running" && cs.startTime ? (Date.now() - cs.startTime) / 1000 : 0);
  const email = cs.email || cs.tokenSub || chainId;

  const cardCls = ["chain-card"];
  const isOaics = cs.linkMode === "oaics";
  if (isOaics) cardCls.push("oaics");
  if (cs.status === "running") cardCls.push("running");
  else if (cs.status === "success") cardCls.push("success");
  else if (cs.status === "failed") cardCls.push("failed");

  const order = isOaics ? OAICS_STAGE_ORDER : STAGE_ORDER;
  const shortMap: Record<string, string> = isOaics ? OAICS_STAGE_SHORT : STAGE_SHORT;
  const cnMap: Record<string, string> = isOaics ? OAICS_STAGE_CN : STAGE_CN;

  const stages = order.map((stage) => {
    const sd = cs.stages[stage];
    let cls = "";
    if (sd?.state === "ok") cls = "ok";
    else if (sd?.state === "fail") cls = "fail";
    else if (sd?.state === "run") cls = "run";
    const label = stageLabel(sd);
    const driftTip = sd?.drifted
      ? ` (配置 ${sd.country} → 实际 ${sd.actualCountry})`
      : sd?.reusedFrom
        ? ` ⇄ 复用 ${sd.reusedFrom} 段出口 IP`
        : sd?.exitIp
          ? ` · 出口 ${sd.exitIp}${sd.geoConfidence ? ` · 置信 ${Math.round((sd.geoConfidence || 0) * 100)}%` : ""}`
          : "";
    return (
      <div key={stage} className={`stage-cell ${cls} ${isOaics ? "oaics" : ""}`} title={`${cnMap[stage]}${label ? " · " + label : ""}${driftTip}`}>
        <span className="stage-dot" />
        <span className="stage-name">{shortMap[stage]}</span>
        <span className="stage-try">
          {sd?.state === "run"
            ? `try ${sd.tryN || 1}/${sd.maxTry || 3}`
            : label}
        </span>
      </div>
    );
  });

  let current: { text: string; cls: string } = { text: "等待开始", cls: "badge-muted" };
  for (const s of order) {
    const st = cs.stages[s];
    if (st?.state === "run") {
      current = { text: `${cnMap[s]} · try ${st.tryN || 1}/${st.maxTry || 3}`, cls: "badge-info" };
      break;
    }
  }
  if (cs.status === "success") current = { text: isOaics ? "✓ OAICS 提链成功" : "✓ 提链成功", cls: "badge-success" };
  else if (cs.status === "failed") current = { text: `✗ ${cs.reasonText || cs.reason || "失败"}`, cls: "badge-danger" };

  const handleClick = () => {
    if (cs.status === "success" && cs.url && onClick) {
      let meta = `chain: ${chainId}`;
      if (cs.email) meta += ` · ${cs.email}`;
      if (cs.country) meta += ` · ${cs.country}`;
      onClick(cs.url, meta);
    }
  };

  return (
    <div
      className={cardCls.join(" ")}
      style={cs.status === "success" && cs.url ? { cursor: "pointer" } : undefined}
      onClick={handleClick}
    >
      <div className="chain-head">
        <span className="tag">#{chainId.slice(0, 8)}</span>
        {isOaics && <span className="tag" style={{ color: "var(--oaics, #3b82f6)", background: "rgba(59,130,246,.12)", border: "1px solid rgba(59,130,246,.35)" }}>OAICS</span>}
        <span className="chain-email" title={email}>{trunc(email)}</span>
        <span className="chain-meta">
          <span>attempt {cs.attempt || 1}</span>
          <span>{fmtDur(elapsed)}</span>
        </span>
      </div>
      <div className="chain-body">
        <div className="pipeline">{stages}</div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 10 }}>
          <span className="geo-chip">
            {cs.actualCountry
              ? (cs.actualCountry !== cs.country
                  ? `${cs.country}→${cs.actualCountry}⚠${cs.exitIp ? ` · ${cs.exitIp}` : ""}`
                  : `${cs.actualCountry}${cs.exitIp ? ` · ${cs.exitIp}` : ""}`)
              : cs.country && `${cs.country}`}
          </span>
          <span className={`badge ${current.cls}`}>{current.text}</span>
        </div>
      </div>
    </div>
  );
}

export const ChainCard = memo(ChainCardInner);