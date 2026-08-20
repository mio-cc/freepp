import { memo, useMemo, useState } from "react";
import { STAGE_ORDER, STAGE_SHORT, STAGE_CN, OAICS_STAGE_CN } from "../../types";
import type { ChainState, StageName, OaicsStageName } from "../../types";

/** OAICS 5 段映射到 7 段列: 不走的段 (init/poll) 为 undefined, 直接跳过 */
const OAICS_COL_MAP: Record<StageName, OaicsStageName | undefined> = {
  checkout: "checkout",
  init: undefined,
  update: "taxes",
  provider: "provider",
  approve: "confirm",
  poll: undefined,
  resolve: "resolve",
};

const fmtDur = (sec: number) => {
  if (sec == null || isNaN(sec)) return "—";
  if (sec < 60) return sec.toFixed(1) + "s";
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}m${s}s`;
};

interface Row {
  id: string;
  cs: ChainState;
}

interface Props {
  chainList: Row[];
  onClick?: (url: string, meta: string) => void;
}

function ChainTableInner({ chainList, onClick }: Props) {
  const [filter, setFilter] = useState<string>("all");

  const counts = useMemo(() => {
    let running = 0, success = 0, failed = 0;
    for (const { cs } of chainList) {
      if (cs.status === "running") running++;
      else if (cs.status === "success") success++;
      else if (cs.status === "failed") failed++;
    }
    return { running, success, failed };
  }, [chainList]);

  const shown = filter === "all"
    ? chainList
    : chainList.filter(({ cs }) => cs.status === filter);

  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">链路列表（{chainList.length}）</span>
        <div style={{ display: "flex", gap: 4 }}>
          {(
            [
              ["all", `全部 ${chainList.length}`],
              ["running", `活跃 ${counts.running}`],
              ["success", `成功 ${counts.success}`],
              ["failed", `失败 ${counts.failed}`],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              className={`btn btn-sm ${filter === k ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setFilter(k)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 80 }}>链路</th>
              <th>Email / Sub</th>
              {STAGE_ORDER.map((s) => (
                <th key={s} style={{ textAlign: "center", minWidth: 58 }} title={STAGE_CN[s]}>
                  <span style={{ color: "var(--text-2)" }}>{STAGE_SHORT[s]}</span>
                </th>
              ))}
              <th style={{ width: 64 }}>耗时</th>
              <th style={{ width: 110 }}>状态</th>
              {onClick && <th style={{ width: 64 }}>操作</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map(({ id, cs }) => {
              const email = cs.email || cs.tokenSub || id;
              // 终端状态使用后端固化的 elapsed, 运行中才实时计时
              const elapsed =
                cs.elapsed ??
                (cs.status === "running" && cs.startTime ? (Date.now() - cs.startTime) / 1000 : 0);
              const handleClick = () => {
                if (cs.status === "success" && cs.url && onClick) {
                  let meta = `chain: ${id}`;
                  if (cs.email) meta += ` · ${cs.email}`;
                  if (cs.country) meta += ` · ${cs.country}`;
                  onClick(cs.url, meta);
                }
              };
              return (
                <tr key={id} className={cs.status === "running" ? "row-selected" : ""}>
                  <td>
                    <span className="tag">#{id.slice(0, 8)}</span>
                    {cs.linkMode === "oaics" && (
                      <span className="tag" style={{ color: "var(--oaics)", background: "var(--oaics-soft)", border: "1px solid var(--oaics-soft)", fontSize: 10, marginLeft: 4 }}>OAICS</span>
                    )}
                    {cs.channelDetect && (
                      <div className="cell-sub" style={{ marginTop: 2 }}>
                        <span
                          className={`badge ${cs.channelDetect.present ? "badge-success" : "badge-danger"}`}
                          title={`渠道探测: ${cs.channelDetect.channel} @ ${cs.channelDetect.country || ""} · types: ${(cs.channelDetect.methods || []).join(", ") || "无"}`}
                          style={{ fontSize: 10 }}
                        >
                          {cs.channelDetect.channel}
                          {cs.channelDetect.present ? " ✓" : " ✗"}
                        </span>
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="cell-strong" style={{ fontSize: 12 }}>
                      {email}
                    </div>
                    <div className="cell-sub">
                      attempt {cs.attempt || 1}
                    </div>
                  </td>
                  {STAGE_ORDER.map((s) => {
                    const isOaics = cs.linkMode === "oaics";
                    let oaicsSrc: OaicsStageName | undefined;
                    if (isOaics) oaicsSrc = OAICS_COL_MAP[s];
                    const sd = isOaics
                      ? (oaicsSrc ? cs.stages[oaicsSrc] : undefined)
                      : cs.stages[s];
                    const act = (sd?.actualCountry || "").toUpperCase();
                    const req = (sd?.country || "").toUpperCase();
                    const drifted = !!act && !!req && act !== req;
                    const geoTail = act
                      ? ` · 真实 ${act}${sd?.exitIp ? ` (${sd.exitIp})` : ""}${sd?.geoConfidence ? ` ${Math.round((sd.geoConfidence || 0) * 100)}%` : ""}`
                      : "";
                    const baseLabel = isOaics && oaicsSrc
                      ? `${OAICS_STAGE_CN[oaicsSrc]} (OAICS)`
                      : `${STAGE_CN[s]}`;
                    const title = `${baseLabel}${req ? ` · 配置 ${req}` : ""}${drifted ? ` → 真实 ${act} ⚠` : geoTail}`;
                    let cls = "stage-cell chain-cell" + (isOaics ? " oaics" : "") + (drifted ? " drift" : "");
                    let label = "";
                    if (!oaicsSrc && isOaics) {
                      // oaics 不走的段: 直接跳过
                      label = "·";
                    } else if (sd?.state === "ok") {
                      cls += " ok";
                      label = drifted ? `${sd.country || "✓"}→${act}⚠` : (sd.country || "✓");
                    } else if (sd?.state === "fail") {
                      cls += " fail";
                      label = "✗";
                    } else if (sd?.state === "run") {
                      cls += " run";
                      label = `try ${sd.tryN || 1}/${sd.maxTry || 3}`;
                    } else {
                      label = "·";
                    }
                    return (
                      <td key={s} style={{ textAlign: "center" }}>
                        <span className={cls} title={title}>
                          <span className="stage-dot" />
                          <span className="stage-try">{label}</span>
                        </span>
                      </td>
                    );
                  })}
                  <td>
                    <span className="muted" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                      {fmtDur(elapsed)}
                    </span>
                  </td>
                  <td>
                    {cs.status === "success" ? (
                      <span className="badge badge-success">✓ 成功</span>
                    ) : cs.status === "failed" ? (
                      <span className="badge badge-danger" title={cs.reasonText || cs.reason || ""}>
                        ✗ {cs.reasonText || cs.reason || "失败"}
                      </span>
                    ) : cs.status === "running" ? (
                      <span className="badge badge-info">运行中</span>
                    ) : (
                      <span className="badge badge-muted">{cs.status || "等待"}</span>
                    )}
                  </td>
                  {onClick && (
                    <td style={{ textAlign: "center" }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        disabled={cs.status !== "success" || !cs.url}
                        onClick={handleClick}
                      >
                        BA
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export const ChainTable = memo(ChainTableInner);
