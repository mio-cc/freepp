import { useState } from "react";
import { useStore } from "../../store/useStore";
import { MAX_CHAIN_CONCURRENCY } from "../../types";
import { api } from "../../api/client";

// 字节自适应格式化: B → KB → MB → GB
function formatBytes(n: number): string {
  if (!n || n < 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(2)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

// 流量分块标签 (顺序固定: 注册/提链/支付/检测)
const TRAFFIC_BLOCKS: { key: string; label: string }[] = [
  { key: "register", label: "注册" },
  { key: "chain", label: "提链" },
  { key: "pay", label: "支付" },
  { key: "detect", label: "检测" },
];

export function TitleBar() {
  const wsStatus = useStore((s) => s.wsStatus);
  const batchRunning = useStore((s) => s.batchRunning);
  const chainStates = useStore((s) => s.chainStates);
  const tokens = useStore((s) => s.tokens);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const traffic = useStore((s) => s.traffic);

  const [trafficOpen, setTrafficOpen] = useState(false);

  const active = Object.values(chainStates).filter((c) => c.status === "running").length;
  const maxConc = MAX_CHAIN_CONCURRENCY;

  // 流量总计: 各功能块上传/下传之和
  const trafficBlocks = Object.values(traffic);
  const totalUp = trafficBlocks.reduce((s, t) => s + (t?.up || 0), 0);
  const totalDown = trafficBlocks.reduce((s, t) => s + (t?.down || 0), 0);

  const resetTraffic = async (block?: string) => {
    try { await api("/api/proxy/traffic/reset", "POST", block ? { block } : {}); } catch { /* ignore */ }
  };

  const wsMap: Record<string, { ind: string; label: string }> = {
    online: { ind: "ind-green", label: "在线" },
    offline: { ind: "ind-grey", label: "离线" },
    connecting: { ind: "ind-orange", label: "连接中" },
    error: { ind: "ind-red", label: "错误" },
  };
  const ws = wsMap[wsStatus] || wsMap.offline;

  // 主题切换: light → dark → system 循环
  const cycleTheme = () => {
    const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
    setTheme(next);
  };
  const themeIcon =
    theme === "dark" ? "🌙" : theme === "light" ? "☀️" : "🖥️";
  const themeTitle =
    theme === "dark" ? "暗色 (点击切换跟随系统)" : theme === "light" ? "亮色 (点击切换暗色)" : "跟随系统 (点击切换亮色)";

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
        <span className="titlebar-divider" />
        <div
          className="titlebar-traffic-wrap"
          onMouseEnter={() => setTrafficOpen(true)}
          onMouseLeave={() => setTrafficOpen(false)}
        >
          <button
            type="button"
            className="titlebar-badge titlebar-traffic"
            onClick={() => resetTraffic()}
            title="代理流量总计 (点击清零)"
          >
            <span style={{ color: "var(--ok)" }}>↑{formatBytes(totalUp)}</span>
            <span style={{ color: "var(--accent)" }}>↓{formatBytes(totalDown)}</span>
          </button>
          {trafficOpen && (
            <div className="traffic-popover" role="tooltip">
              <div className="traffic-popover-head">
                <span>代理流量分块明细</span>
                <button
                  type="button"
                  className="traffic-popover-reset"
                  onClick={() => resetTraffic()}
                >
                  全部清零
                </button>
              </div>
              <div className="traffic-popover-grid">
                {TRAFFIC_BLOCKS.map(({ key, label }) => {
                  const b = traffic[key] || { up: 0, down: 0 };
                  return (
                    <div className="traffic-popover-row" key={key}>
                      <span className="traffic-popover-label">{label}</span>
                      <span className="traffic-popover-up" style={{ color: "var(--ok)" }}>
                        ↑{formatBytes(b.up)}
                      </span>
                      <span className="traffic-popover-down" style={{ color: "var(--accent)" }}>
                        ↓{formatBytes(b.down)}
                      </span>
                      <button
                        type="button"
                        className="traffic-popover-reset-sm"
                        onClick={() => resetTraffic(key)}
                        title={`清零 ${label}`}
                      >
                        清
                      </button>
                    </div>
                  );
                })}
              </div>
              <div className="traffic-popover-foot">
                <span>总计</span>
                <span style={{ color: "var(--ok)" }}>↑{formatBytes(totalUp)}</span>
                <span style={{ color: "var(--accent)" }}>↓{formatBytes(totalDown)}</span>
              </div>
            </div>
          )}
        </div>
        <span className="titlebar-divider" />
        <button
          type="button"
          className="theme-toggle"
          onClick={cycleTheme}
          title={themeTitle}
          aria-label={themeTitle}
        >
          {themeIcon}
        </button>
      </div>
    </header>
  );
}
