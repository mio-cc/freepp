import { memo } from "react";
import { STAGE_ORDER, STAGE_SHORT, STAGE_CN } from "../../types";
import type { ChainState } from "../../types";

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

interface Props {
  chainId: string;
  cs: ChainState;
  onClick?: (url: string, meta: string) => void;
}

function ChainCardInner({ chainId, cs, onClick }: Props) {
  const elapsed = cs.startTime ? (Date.now() - cs.startTime) / 1000 : 0;
  const email = cs.email || cs.tokenSub || chainId;

  const cardCls = ["chain-card"];
  if (cs.status === "running") cardCls.push("running");
  else if (cs.status === "success") cardCls.push("success");
  else if (cs.status === "failed") cardCls.push("failed");

  const stages = STAGE_ORDER.map((stage) => {
    const sd = cs.stages[stage];
    let cls = "";
    if (sd?.state === "ok") cls = "ok";
    else if (sd?.state === "fail") cls = "fail";
    else if (sd?.state === "run") cls = "run";
    return (
      <div key={stage} className={`stage-cell ${cls}`} title={`${STAGE_CN[stage]}${sd?.country ? " · " + sd.country : ""}`}>
        <span className="stage-dot" />
        <span className="stage-name">{STAGE_SHORT[stage]}</span>
        <span className="stage-try">
          {sd?.state === "run"
            ? `try ${sd.tryN || 1}/${sd.maxTry || 3}`
            : sd?.country || ""}
        </span>
      </div>
    );
  });

  let current: { text: string; cls: string } = { text: "等待开始", cls: "badge-muted" };
  for (const s of STAGE_ORDER) {
    const st = cs.stages[s];
    if (st?.state === "run") {
      current = { text: `${STAGE_CN[s]} · try ${st.tryN || 1}/${st.maxTry || 3}`, cls: "badge-info" };
      break;
    }
  }
  if (cs.status === "success") current = { text: "✓ 提链成功", cls: "badge-success" };
  else if (cs.status === "failed") current = { text: `✗ ${cs.reason || "失败"}`, cls: "badge-danger" };

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
        <span className="chain-email" title={email}>{trunc(email)}</span>
        <span className="chain-meta">
          <span>attempt {cs.attempt || 1}</span>
          <span>{fmtDur(elapsed)}</span>
        </span>
      </div>
      <div className="chain-body">
        <div className="pipeline">{stages}</div>
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
          <span className={`badge ${current.cls}`}>{current.text}</span>
        </div>
      </div>
    </div>
  );
}

export const ChainCard = memo(ChainCardInner);
