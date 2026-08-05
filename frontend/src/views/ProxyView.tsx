import { useEffect, useState, useCallback } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { ProxyNode } from "../types";

/** 711 代理池状态 (只读禁改) */
interface Proxy711Status {
  enabled: boolean;
  healthy: boolean;
  readonly: boolean;
  gateway_host: string;
  gateway_port: number;
  default_user: string;
  relay_host: string;
  relay_port: number;
  clash_addr: string;
  clash_candidates: string[];
  supported_countries: string[];
  active_sessions: number;
  sessions: Array<{
    id: string;
    region: string;
    sess_time: number;
    sticky: boolean;
    age_sec: number;
  }>;
  exit_ip: string;
  chain: string;
  last_check: number;
}

function flag(cc: string): string {
  if (!cc || cc.length !== 2) return "";
  const cp = 0x1f1e6 + (cc.charCodeAt(0) - 65) * 0x100 + (cc.charCodeAt(1) - 65);
  return String.fromCodePoint(cp);
}

export function ProxyView() {
  const nodes = useStore((s) => s.nodes);
  const qgPool = useStore((s) => s.qgPool);
  const pushLog = useStore((s) => s.pushLog);

  const [subUrl, setSubUrl] = useState("");
  const [subRaw, setSubRaw] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [status711, setStatus711] = useState<Proxy711Status | null>(null);
  const [smokeBusy, setSmokeBusy] = useState(false);
  const [smokeResult, setSmokeResult] = useState("");

  const load711 = useCallback(async () => {
    try {
      const r = await api("/api/proxy/711/status");
      if (r) {
        const { ok, ...rest } = r;
        setStatus711(rest as Proxy711Status);
      }
    } catch {
      setStatus711(null);
    }
  }, []);

  useEffect(() => {
    load711();
  }, [load711]);

  const handleSmoke = async () => {
    setSmokeBusy(true);
    setSmokeResult("测试中...");
    try {
      const r = await api("/api/proxy/711/smoke", "POST");
      if (r?.result) {
        const healthy = r.result.healthy;
        setSmokeResult(healthy ? "✓ 链路正常" : "✗ 链路异常");
        pushLog(`711 冒烟测试: ${healthy ? "成功" : "失败"}`, healthy ? "ok" : "err");
        await load711();
      } else {
        setSmokeResult("无返回");
      }
    } catch (e) {
      setSmokeResult("失败: " + (e as Error).message);
      pushLog("711 冒烟测试失败", "err");
    } finally {
      setSmokeBusy(false);
    }
  };

  const handleFetchSub = async () => {
    if (!subUrl.trim()) {
      setResult("请输入订阅 URL");
      return;
    }
    setBusy(true);
    try {
      const r = await api("/api/proxy/fetch-sub", "POST", { url: subUrl });
      if (r && typeof r.raw === "string") {
        setSubRaw(r.raw);
        setResult(`已获取 ${r.length ?? r.raw.length} 字节`);
      } else {
        setResult("未返回内容");
      }
    } catch (e) {
      setResult("拉取失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleParse = async () => {
    if (!subRaw.trim()) {
      setResult("请粘贴或拉取订阅内容");
      return;
    }
    setBusy(true);
    try {
      const r = await api("/api/proxy/parse", "POST", { raw: subRaw });
      if (r && Array.isArray(r.nodes)) {
        useStore.setState({ nodes: r.nodes });
        setResult(`解析完成: ${r.count ?? r.nodes.length} 个节点`);
      } else {
        setResult("解析失败");
      }
    } catch (e) {
      setResult("解析失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleHealth = async () => {
    setBusy(true);
    try {
      const r = await api("/api/proxy/health");
      if (r && Array.isArray(r.nodes)) {
        useStore.setState({ nodes: r.nodes });
        setResult("健康检查完成");
      }
      await load711();
    } catch (e) {
      setResult("健康检查失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleStart = async (name: string) => {
    try {
      const r = await api("/api/proxy/start", "POST", { name });
      if (r && Array.isArray(r.nodes)) useStore.setState({ nodes: r.nodes });
      pushLog(`启动节点: ${name}`, "info");
    } catch (e) {
      pushLog("启动失败: " + (e as Error).message, "err");
    }
  };

  const handleStop = async (name: string) => {
    try {
      const r = await api("/api/proxy/stop", "POST", { name });
      if (r && Array.isArray(r.nodes)) useStore.setState({ nodes: r.nodes });
      pushLog(`停止节点: ${name}`, "info");
    } catch (e) {
      pushLog("停止失败: " + (e as Error).message, "err");
    }
  };

  const handleStartAll = async () => {
    setBusy(true);
    try {
      const r = await api("/api/proxy/start-all", "POST");
      if (r && Array.isArray(r.nodes)) useStore.setState({ nodes: r.nodes });
      setResult(`已启动 ${r.started ?? 0} 个节点`);
    } catch (e) {
      setResult("启动失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleStopAll = async () => {
    setBusy(true);
    try {
      const r = await api("/api/proxy/stop-all", "POST");
      if (r && Array.isArray(r.nodes)) useStore.setState({ nodes: r.nodes });
      setResult(`已停止 ${r.stopped ?? 0} 个节点`);
    } catch (e) {
      setResult("停止失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const nodeByCountry = nodes.reduce<Record<string, number>>((acc, n) => {
    const c = n.country_hint || "?";
    acc[c] = (acc[c] || 0) + 1;
    return acc;
  }, {});
  const healthyCount = nodes.filter((n) => n.healthy === true).length;
  const runningCount = nodes.filter((n) => n.running).length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">代理池</h2>
          <p className="page-sub">711 住宅代理 (主) · sing-box 节点 · QG 隧道 (备)</p>
        </div>
        <div className="page-actions">
          <button className="btn" onClick={handleHealth} disabled={busy}>
            健康检查
          </button>
          <button className="btn btn-primary" onClick={handleStartAll} disabled={busy}>
            全部启动
          </button>
          <button className="btn btn-danger" onClick={handleStopAll} disabled={busy}>
            全部停止
          </button>
        </div>
      </div>

      {/* ===== 711 住宅代理池 (主代理 — 只读禁改) ===== */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">711 住宅代理池</span>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span className="badge badge-warn">只读禁改</span>
            <span className="card-hint">主代理 · client → relay → Clash → 711 → target</span>
            {status711 && (
              <span className={`health-dot ${status711.healthy ? "healthy" : "unhealthy"}`} />
            )}
            <button
              className="btn btn-sm"
              onClick={handleSmoke}
              disabled={smokeBusy || !status711?.enabled}
            >
              {smokeBusy ? "测试中..." : "冒烟测试"}
            </button>
            {smokeResult && (
              <span
                style={{
                  color: smokeResult.startsWith("✓") ? "var(--ok)" : "var(--danger)",
                  fontSize: 11,
                }}
              >
                {smokeResult}
              </span>
            )}
          </div>
        </div>

        {status711 ? (
          <>
            <div className="flow-chain">
              <span className="muted">client</span>
              <span className="flow-arrow">→</span>
              <span className="flow-node">{status711.relay_host}:{status711.relay_port}</span>
              <span className="flow-arrow">→</span>
              <span className="flow-node">Clash ({status711.clash_addr || "7897"})</span>
              <span className="flow-arrow">→</span>
              <span className="flow-node accent">
                {status711.gateway_host}:{status711.gateway_port}
              </span>
              <span className="flow-arrow">→</span>
              <span className="muted">target</span>
            </div>

            <div className="detail-grid">
              <div className="detail-cell">
                <div className="dc-label">网关</div>
                <div className="dc-value">{status711.gateway_host}:{status711.gateway_port}</div>
              </div>
              <div className="detail-cell">
                <div className="dc-label">中继端口</div>
                <div className="dc-value">{status711.relay_host}:{status711.relay_port}</div>
              </div>
              <div className="detail-cell">
                <div className="dc-label">Clash 端口</div>
                <div className="dc-value">{status711.clash_addr || "未探测"}</div>
                <div style={{ fontSize: 10, color: "var(--text-3)" }}>
                  候选: {status711.clash_candidates?.join(" / ") || "7897"}
                </div>
              </div>
              <div className="detail-cell">
                <div className="dc-label">活跃 Session</div>
                <div className="dc-value" style={{ color: "var(--accent-strong)" }}>
                  {status711.active_sessions}
                </div>
              </div>
            </div>

            <div className="card-body">
              <div className="section-head">
                <span className="section-title">支持国家 (711 住宅代理可达)</span>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {status711.supported_countries?.map((cc) => (
                  <span className="country-tag" key={cc}>
                    {flag(cc)} {cc}
                  </span>
                ))}
              </div>
            </div>

            {status711.sessions && status711.sessions.length > 0 && (
              <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)" }}>
                <div className="section-head">
                  <span className="section-title">
                    活跃 Session (最近 {Math.min(status711.sessions.length, 20)})
                  </span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {status711.sessions.map((s, i) => (
                    <div
                      key={s.id || i}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 60px 60px 60px",
                        gap: 8,
                        fontSize: 11,
                        fontFamily: "var(--font-mono)",
                        padding: "4px 0",
                      }}
                    >
                      <span className="ellipsis">
                        {flag(s.region)} {s.id}
                      </span>
                      <span className="muted">{s.region}</span>
                      <span className="muted">{s.sess_time}s</span>
                      <span className="muted">{s.age_sec}s前</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div
              style={{
                padding: "8px 16px",
                borderTop: "1px solid var(--border-faint)",
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                color: "var(--text-3)",
              }}
            >
              默认用户名: {status711.default_user} · sticky session 格式: session-&lt;sid&gt;-sessTime-&lt;sec&gt;-region-&lt;CC&gt;
            </div>
          </>
        ) : (
          <div className="empty">
            <div className="empty-icon">🔄</div>
            <div className="empty-title">加载 711 状态中...</div>
          </div>
        )}
      </div>

      {/* ===== QG 隧道池 (备代理) ===== */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">QG 隧道池</span>
          <span className="card-hint">备代理 · 青果隧道 · 超级池(机房) + 住宅池</span>
        </div>
        <div className="card-body">
          <div className="grid grid-3">
            <div className="mini-card">
              <div className="mini-card-label">Super 隧道</div>
              <div className="mini-card-value">
                <span className={`badge ${qgPool.superState === "active" ? "badge-success" : "badge-muted"}`}>
                  {qgPool.superState || "unknown"}
                </span>
              </div>
            </div>
            <div className="mini-card">
              <div className="mini-card-label">Resi 隧道</div>
              <div className="mini-card-value">
                <span className={`badge ${qgPool.resiState === "active" ? "badge-success" : "badge-muted"}`}>
                  {qgPool.resiState || "unknown"}
                </span>
              </div>
            </div>
            <div className="mini-card">
              <div className="mini-card-label">默认池</div>
              <div className="mini-card-value">{qgPool.defaultPool || "unknown"}</div>
            </div>
          </div>
        </div>
      </div>

      {/* ===== sing-box 节点订阅 ===== */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">sing-box 节点</span>
          <span className="card-hint">
            {nodes.length} 节点 · 健康 {healthyCount} · 运行 {runningCount} ·{" "}
            {Object.entries(nodeByCountry).map(([c, n]) => `${c}×${n}`).join(" ")}
          </span>
        </div>
        <div className="inline-fields">
          <input
            className="input"
            style={{ flex: 1, minWidth: 200 }}
            placeholder="订阅 URL"
            value={subUrl}
            onChange={(e) => setSubUrl(e.target.value)}
          />
          <button className="btn" onClick={handleFetchSub} disabled={busy}>
            拉取
          </button>
        </div>
        <div style={{ padding: "0 16px 12px" }}>
          <textarea
            className="textarea"
            rows={3}
            placeholder="订阅原始内容 (base64 / JSON / 列表)"
            value={subRaw}
            onChange={(e) => setSubRaw(e.target.value)}
          />
        </div>
        <div className="btn-row">
          <button className="btn btn-primary btn-sm" onClick={handleParse} disabled={busy}>
            解析
          </button>
          {result && <span className="muted">{result}</span>}
        </div>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>国家</th>
              <th>端口</th>
              <th className="num">延迟</th>
              <th>健康</th>
              <th className="num">并发</th>
              <th style={{ textAlign: "right" }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {nodes.length === 0 && (
              <tr>
                <td colSpan={8} className="muted" style={{ textAlign: "center" }}>
                  暂无节点
                </td>
              </tr>
            )}
            {nodes.map((n) => (
              <tr key={n.name}>
                <td className="cell-strong">{n.name}</td>
                <td>
                  <span className="tag">{n.type || "-"}</span>
                </td>
                <td>{flag(n.country_hint)} {n.country_hint || "-"}</td>
                <td className="mono">{n.port ?? "-"}</td>
                <td className="num">{n.latency != null ? `${n.latency} ms` : "-"}</td>
                <td>
                  <span className={`health-dot ${
                    n.healthy === true ? "healthy" : n.healthy === false ? "unhealthy" : ""
                  }`} />
                </td>
                <td className="num">
                  {n.concurrent ?? 0}/{n.max_concurrent ?? 0}
                </td>
                <td style={{ textAlign: "right" }}>
                  {n.running ? (
                    <button className="btn btn-ghost btn-sm" onClick={() => handleStop(n.name)}>
                      停止
                    </button>
                  ) : (
                    <button className="btn btn-ghost btn-sm" onClick={() => handleStart(n.name)}>
                      启动
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
