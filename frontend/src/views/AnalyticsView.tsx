import { useMemo, useState } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import { STAGE_ORDER, STAGE_SHORT, STAGE_CN } from "../types";

function percentile(sorted: number[], p: number): number {
  const n = sorted.length;
  if (n === 0) return 0;
  if (n === 1) return sorted[0];
  const rank = Math.ceil((p / 100) * n);
  const idx = Math.min(Math.max(rank - 1, 0), n - 1);
  return sorted[idx];
}

export function AnalyticsView() {
  const stats = useStore((s) => s.stats);
  const latencies = useStore((s) => s.latencies);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string>("");

  const refresh = async () => {
    setLoading(true);
    setErr("");
    try {
      const data = await api("/api/stats");
      if (data && data.ok) {
        useStore.setState({
          stats: data.stats ?? stats,
          latencies: data.latencies ?? latencies,
        });
      } else {
        setErr((data && data.error) || "加载失败");
      }
    } catch (e: any) {
      setErr((e && e.message) || "网络错误");
    } finally {
      setLoading(false);
    }
  };

  const sortedByCountry = useMemo<[string, number][]>(
    () =>
      Object.entries(stats.byCountry || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 12),
    [stats.byCountry]
  );

  const sortedFailByCountry = useMemo<[string, number][]>(
    () =>
      Object.entries(stats.failByCountry || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 12),
    [stats.failByCountry]
  );

  const sortedReasons = useMemo<[string, number][]>(
    () =>
      Object.entries(stats.reasons || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 12),
    [stats.reasons]
  );

  const { matrixCountries, maxByCountry, maxFailCountry, maxReason } = useMemo(() => {
    const cs = new Set<string>();
    const sm = stats.stageMatrix || {};
    for (const stage of STAGE_ORDER) {
      const row = sm[stage];
      if (row) for (const c of Object.keys(row)) cs.add(c);
    }
    return {
      matrixCountries: Array.from(cs),
      maxByCountry: sortedByCountry.reduce((m, [, v]) => Math.max(m, v), 0),
      maxFailCountry: sortedFailByCountry.reduce((m, [, v]) => Math.max(m, v), 0),
      maxReason: sortedReasons.reduce((m, [, v]) => Math.max(m, v), 0),
    };
  }, [stats.stageMatrix, sortedByCountry, sortedFailByCountry, sortedReasons]);

  const total = stats.success + stats.failure;
  const successRate = total > 0 ? (stats.success / total) * 100 : 0;
  const sortedLat = useMemo(
    () => [...latencies].sort((a, b) => a - b),
    [latencies]
  );
  const p50 = percentile(sortedLat, 50);
  const p95 = percentile(sortedLat, 95);

  const renderBarList = (
    data: [string, number][],
    fillClass: "ok" | "fail",
    max: number
  ) => {
    if (data.length === 0) {
      return <div className="empty" style={{ padding: 24 }}>暂无数据</div>;
    }
    return (
      <div className="bar-list">
        {data.map(([label, count]) => {
          const pct = max > 0 ? (count / max) * 100 : 0;
          return (
            <div className="bar-item" key={label}>
              <span className="bi-label">{label}</span>
              <span className="bi-bar">
                <span className={`bi-fill ${fillClass}`} style={{ width: `${pct}%` }} />
              </span>
              <span className="bi-count">{count}</span>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h2 className="page-title">统计分析</h2>
          <p className="page-sub">成功率、国家分布、失败原因与段级矩阵</p>
        </div>
        <div className="page-actions">
          <button className="btn btn-primary" onClick={refresh} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </button>
        </div>
      </div>

      {err && <div className="alert alert-danger" style={{ marginBottom: 14 }}>{err}</div>}

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <span className="stat-label">成功</span>
          <div className="stat-value" style={{ color: "var(--ok)" }}>{stats.success}</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">失败</span>
          <div className="stat-value" style={{ color: "var(--danger)" }}>{stats.failure}</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">成功率</span>
          <div className="stat-value">{successRate.toFixed(1)}%</div>
        </div>
        <div className="stat-card">
          <span className="stat-label">延迟 P50 / P95</span>
          <div className="stat-value" style={{ fontSize: 20 }}>
            {sortedLat.length === 0 ? (
              <span className="muted" style={{ fontSize: 14 }}>— / —</span>
            ) : (
              <>
                {p50} <span className="muted">/</span> {p95}
              </>
            )}
            <span className="muted" style={{ fontSize: 13 }}> ms</span>
          </div>
        </div>
      </div>

      <div className="analytics-grid">
        <div className="card">
          <div className="card-head">
            <span className="card-title">成功国家分布</span>
          </div>
          {renderBarList(sortedByCountry, "ok", maxByCountry)}
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">失败国家分布</span>
          </div>
          {renderBarList(sortedFailByCountry, "fail", maxFailCountry)}
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">失败原因</span>
          </div>
          {renderBarList(sortedReasons, "fail", maxReason)}
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">段级矩阵</span>
          </div>
          {matrixCountries.length === 0 ? (
            <div className="empty" style={{ padding: 24 }}>暂无数据</div>
          ) : (
            <div className="stage-matrix">
              <table className="sm-table">
                <thead>
                  <tr>
                    <th>阶段</th>
                    {matrixCountries.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {STAGE_ORDER.map((stage) => {
                    const row = stats.stageMatrix?.[stage] || {};
                    return (
                      <tr key={stage}>
                        <th>
                          {STAGE_SHORT[stage]}{" "}
                          <span className="muted" style={{ fontWeight: 400 }}>{STAGE_CN[stage]}</span>
                        </th>
                        {matrixCountries.map((c) => {
                          const cell = row[c] || { ok: 0, fail: 0 };
                          return (
                            <td key={c}>
                              <span className="sm-cell-ok">{cell.ok}</span>
                              <span className="muted"> / </span>
                              <span className="sm-cell-fail">{cell.fail}</span>
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
