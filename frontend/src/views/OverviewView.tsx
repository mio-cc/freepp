import { useMemo } from "react";
import { useStore } from "../store/useStore";
import { STAGE_ORDER, MAX_CHAIN_CONCURRENCY } from "../types";

export function OverviewView() {
  const chainStates = useStore((s) => s.chainStates);
  const stats = useStore((s) => s.stats);
  const latencies = useStore((s) => s.latencies);
  const tokens = useStore((s) => s.tokens);
  const logLines = useStore((s) => s.logLines);
  const selectedTokenIds = useStore((s) => s.selectedTokenIds);
  const pushLog = useStore((s) => s.pushLog);
  const setView = useStore((s) => s.setView);

  const chainList = useMemo(
    () => Object.entries(chainStates).map(([id, c]) => ({ id, ...c })),
    [chainStates]
  );

  const activeCount = useMemo(
    () => chainList.filter((c) => c.status === "running").length,
    [chainList]
  );

  const successCount = stats.success;
  const failedCount = stats.failure;
  const totalCount = successCount + failedCount;
  const successRate = totalCount > 0 ? (successCount / totalCount) * 100 : 0;

  const p95 = useMemo(() => {
    if (latencies.length === 0) return 0;
    const sorted = [...latencies].sort((a, b) => a - b);
    const idx = Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95));
    return sorted[idx];
  }, [latencies]);

  const topChains = useMemo(() => {
    return [...chainList]
      .sort((a, b) => {
        const aRunning = a.status === "running" ? 1 : 0;
        const bRunning = b.status === "running" ? 1 : 0;
        if (aRunning !== bRunning) return bRunning - aRunning;
        return b.startTime - a.startTime;
      })
      .slice(0, 8);
  }, [chainList]);

  const recentLogs = useMemo(() => logLines.slice(-15), [logLines]);

  const activeRatio =
    MAX_CHAIN_CONCURRENCY > 0 ? (activeCount / MAX_CHAIN_CONCURRENCY) * 100 : 0;

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h2 className="page-title">总览</h2>
          <p className="page-sub">系统状态概览与关键指标</p>
        </div>
      </div>

      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <span className="stat-label">活跃链路</span>
          <div className="stat-value">
            {activeCount}
            <span style={{ color: "var(--text-3)", fontSize: 14 }}> / {MAX_CHAIN_CONCURRENCY}</span>
          </div>
          <div className="stat-foot">
            <div className="progress" style={{ flex: 1 }}>
              <div className="progress-bar" style={{ width: `${Math.min(100, activeRatio)}%` }} />
            </div>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-label">成功</span>
          <div className="stat-value" style={{ color: "var(--ok)" }}>{successCount}</div>
          <div className="stat-foot">累计提链成功</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">失败</span>
          <div className="stat-value" style={{ color: "var(--danger)" }}>{failedCount}</div>
          <div className="stat-foot">累计链路失败</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">成功率</span>
          <div className="stat-value">{successRate.toFixed(1)}%</div>
          <div className="stat-sub">
            <span>成功 <b>{successCount}</b></span>
            <span>失败 <b>{failedCount}</b></span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-label">P95 延迟</span>
          <div className="stat-value">
            {p95.toFixed(0)}
            <span style={{ color: "var(--text-3)", fontSize: 14 }}> ms</span>
          </div>
          <div className="stat-foot">样本 {latencies.length} 条</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">Token 数</span>
          <div className="stat-value">{tokens.length}</div>
          <div className="stat-foot">已选择 {selectedTokenIds.size}</div>
        </div>
      </div>

      <div className="grid grid-main">
        <div className="card">
          <div className="card-head">
            <span className="card-title">活跃链路</span>
            <button className="btn btn-ghost btn-sm" onClick={() => setView("chains")}>
              查看全部 →
            </button>
          </div>
          {topChains.length === 0 ? (
            <div className="empty">
              <div className="empty-icon">⏳</div>
              <div className="empty-title">暂无活跃链路</div>
              <div className="empty-hint">在 Token 库选择令牌后点击「批量启动」开始提链</div>
            </div>
          ) : (
            <div className="mini-chains">
              {topChains.map((c) => (
                <div className="mini-chain" key={c.id}>
                  <span className="mc-id">#{c.id.slice(0, 8)}</span>
                  <span className="mc-email">{c.email || c.tokenSub || "—"}</span>
                  <span className="mini-dots">
                    {STAGE_ORDER.map((stage) => {
                      const sd = c.stages[stage];
                      return (
                        <span
                          className={`mini-dot${sd ? ` ${sd.state}` : ""}`}
                          key={stage}
                          title={stage}
                        />
                      );
                    })}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">最近事件</span>
            <button className="btn btn-ghost btn-sm" onClick={() => setView("logs")}>
              查看全部 →
            </button>
          </div>
          {recentLogs.length === 0 ? (
            <div className="empty">
              <div className="empty-icon">📡</div>
              <div className="empty-title">暂无日志</div>
            </div>
          ) : (
            <div className="mini-log">
              {recentLogs.map((l, i) => (
                <div className={`ml ${l.level}`} key={i}>
                  <span className="ts">{l.ts}</span>
                  <span>{l.msg}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
