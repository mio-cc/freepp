import { useEffect, useState } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { Sample } from "../types";

function CountryCell({ s }: { s: Sample }) {
  const act = s.actual_country || "";
  const req = s.requested_country || s.country || "";
  if (act && req && act !== req) {
    return (
      <span className="tag" title={`配置 ${req} → 实际 ${act}${s.exit_ip ? `, 出口 ${s.exit_ip}` : ""}`}>
        {req}→{act}⚠
      </span>
    );
  }
  if (act) {
    return (
      <span className="tag" title={s.exit_ip ? `出口 ${s.exit_ip}${s.geo_confidence ? `, 置信 ${Math.round((s.geo_confidence || 0) * 100)}%` : ""}` : undefined}>
        {act}
      </span>
    );
  }
  return <span className="tag">{req || "—"}</span>;
}

export function SamplesView() {
  const sampleTab = useStore((s) => s.sampleTab);
  const setSampleTab = useStore((s) => s.setSampleTab);
  const samples = useStore((s) => s.samples);
  const samplesLoaded = useStore((s) => s.samplesLoaded);
  const samplesError = useStore((s) => s.samplesError);

  useEffect(() => {
    const cur = useStore.getState();
    if (cur.samplesLoaded[sampleTab]) return;
    let cancelled = false;

    (async () => {
      let list: Sample[] = [];
      let loadError = "";
      try {
        const data = await api(`/api/samples?success=${sampleTab === "success"}`);
        if (cancelled) return;
        if (data && data.ok && Array.isArray(data.samples)) {
          list = data.samples;
        }
      } catch (e) {
        loadError = (e as Error).message;
      }

      if (cancelled) return;
      const latest = useStore.getState();
      useStore.setState({
        samples: { ...latest.samples, [sampleTab]: list },
        samplesLoaded: { ...latest.samplesLoaded, [sampleTab]: true },
        samplesError: { ...latest.samplesError, [sampleTab]: loadError },
      });
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sampleTab]);

  const switchTab = (t: "success" | "failure") => {
    if (t !== sampleTab) {
      setSelected(new Set()); // 切 tab 时清空选中
      setSampleTab(t);
    }
  };

  const list: Sample[] = samples[sampleTab] || [];
  const loaded = samplesLoaded[sampleTab];

  /* ── 批量管理 ── */
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const allSelected = list.length > 0 && list.every((s) => s.id != null && selected.has(s.id!));
  const toggleSelect = (id: number) =>
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleSelectAll = () =>
    setSelected(allSelected ? new Set() : new Set(list.map((s) => s.id!).filter((id) => id != null)));

  async function bulkDelete() {
    const ids = Array.from(selected);
    if (!ids.length) return;
    if (!window.confirm(`确认删除选中的 ${ids.length} 条样本？此操作不可撤销。`)) return;
    setDeleting(true);
    try {
      const r = await api<{ ok: boolean; deleted?: number; error?: string }>("/api/samples/bulk_delete", "POST", { ids });
      if (r?.ok) {
        setSelected(new Set());
        // 强制重新加载当前 tab
        useStore.setState((st) => ({
          samplesLoaded: { ...st.samplesLoaded, [sampleTab]: false },
        }));
      } else {
        window.alert(`删除失败: ${r?.error || "未知"}`);
      }
    } catch (e) {
      window.alert("删除失败: " + (e as Error).message);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h2 className="page-title">样本记录</h2>
          <p className="page-sub">查看成功与失败样本明细</p>
        </div>
        <div className="page-actions">
          <div className="tabs">
            <button
              className={`tab ${sampleTab === "success" ? "active" : ""}`}
              onClick={() => switchTab("success")}
            >
              成功样本
            </button>
            <button
              className={`tab ${sampleTab === "failure" ? "active" : ""}`}
              onClick={() => switchTab("failure")}
            >
              失败样本
            </button>
          </div>
        </div>
      </div>

      {!loaded && list.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon">🔄</div>
            <div className="empty-title">加载中…</div>
          </div>
        </div>
      ) : list.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon">📄</div>
            <div className="empty-title">暂无数据</div>
            {samplesError[sampleTab] && (
              <div className="empty-hint" style={{ color: "var(--danger)" }}>
                加载失败: {samplesError[sampleTab]}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
            {selected.size > 0 && (
              <div className="batch-bar">
                <span className="tag">已选 {selected.size}</span>
                <button className="btn btn-sm btn-danger" onClick={bulkDelete} disabled={deleting}>
                  {deleting ? "删除中…" : "删除所选"}
                </button>
                <button className="btn btn-sm btn-ghost" onClick={() => setSelected(new Set())}>取消选择</button>
              </div>
            )}
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}>
                    <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
                  </th>
                  <th>Chain ID</th>
                  <th>Email</th>
                  <th>国家</th>
                  <th>时间</th>
                  {sampleTab === "success" ? (
                    <th>PayPal Approve URL</th>
                  ) : (
                    <>
                      <th>失败阶段</th>
                      <th>原因</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {sampleTab === "success"
                  ? list.map((s, i) => (
                      <tr key={s.chain_id || i} className={s.id != null && selected.has(s.id) ? "row-selected" : ""}>
                        <td><input type="checkbox" checked={s.id != null && selected.has(s.id)} onChange={() => s.id != null && toggleSelect(s.id)} /></td>
                        <td className="mono">{s.chain_id}</td>
                        <td>{s.email}</td>
                        <td><CountryCell s={s} /></td>
                        <td className="mono">{s.ts.replace("T", " ").slice(0, 19)}</td>
                        <td className="mono" style={{ maxWidth: 320 }} title={s.paypal_approve_url}>
                          <span className="ellipsis" style={{ display: "inline-block", maxWidth: 320, verticalAlign: "bottom" }}>
                            {s.paypal_approve_url}
                          </span>
                        </td>
                      </tr>
                    ))
                  : list.map((s, i) => (
                      <tr key={s.chain_id || i} className={s.id != null && selected.has(s.id) ? "row-selected" : ""}>
                        <td><input type="checkbox" checked={s.id != null && selected.has(s.id)} onChange={() => s.id != null && toggleSelect(s.id)} /></td>
                        <td className="mono">{s.chain_id}</td>
                        <td>{s.email}</td>
                        <td><CountryCell s={s} /></td>
                        <td className="mono">{s.ts.replace("T", " ").slice(0, 19)}</td>
                        <td><span className="tag">{s.stage_reached}</span></td>
                        <td>
                          <span className="badge badge-danger">{s.reason_code}</span>
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
