import { useStore } from "../../store/useStore";

export function TitleBar() {
  const wsStatus = useStore((s) => s.wsStatus);
  const batchRunning = useStore((s) => s.batchRunning);
  const chainStates = useStore((s) => s.chainStates);
  const tokens = useStore((s) => s.tokens);

  const active = Object.values(chainStates).filter((c) => c.status === "running").length;
  const maxConc = 10; // could be from settings

  const wsMap: Record<string, { ind: string; label: string }> = {
    online: { ind: "ind-green", label: "在线" },
    offline: { ind: "ind-grey", label: "离线" },
    connecting: { ind: "ind-orange", label: "连接中" },
    error: { ind: "ind-red", label: "错误" },
  };
  const ws = wsMap[wsStatus] || wsMap.offline;

  return (
    <header className="titlebar">
      <div className="traffic-lights">
        <span className="tl tl-close" />
        <span className="tl tl-min" />
        <span className="tl tl-max" />
      </div>
      <div className="titlebar-title">
        <span className="titlebar-mark" />
        <span className="titlebar-name">Min-Implant</span>
        <span className="titlebar-sep">·</span>
        <span className="titlebar-sub">提链引擎 v2</span>
      </div>
      <div className="titlebar-actions">
        <span className="titlebar-badge">
          <span className={`ind ${ws.ind}`} />{ws.label}
        </span>
        <span className="titlebar-divider" />
        <span className="titlebar-badge">
          <span className={`ind ${batchRunning ? "ind-blue" : "ind-grey"}`} />
          {batchRunning ? "运行中" : "空闲"}
        </span>
        <span className="titlebar-divider" />
        <span className="titlebar-badge">
          <span className={`ind ${active > 0 ? "ind-blue" : "ind-grey"}`} />
          {active}/{maxConc}
        </span>
      </div>
    </header>
  );
}
