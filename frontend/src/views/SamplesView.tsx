import { useEffect } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import { STAGE_ORDER } from "../types";
import type { Sample } from "../types";

const COUNTRIES = ["US", "JP", "GB", "AU", "HK", "DE", "BR", "VN"];
const REASONS = [
  "proxy_timeout",
  "dns_fail",
  "amount_guard",
  "poll_timeout",
  "stripe_card_declined",
];

function pick<T>(arr: readonly T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function genSuccessSamples(n: number): Sample[] {
  const out: Sample[] = [];
  for (let i = 0; i < n; i++) {
    const country = pick(COUNTRIES);
    out.push({
      ts: new Date(Date.now() - i * 60000).toISOString(),
      email: `user${i}@example.com`,
      success: true,
      reason_code: "",
      reason_text: "",
      paypal_approve_url: `https://www.paypal.com/checkoutnow?token=EC-MOCK${1000 + i}&c=${country}`,
      amount_due: 0,
      currency: "USD",
      country,
      stage_reached: "resolve",
      chain_id: `C1000${i}`,
    });
  }
  return out;
}

function genFailureSamples(n: number): Sample[] {
  const out: Sample[] = [];
  for (let i = 0; i < n; i++) {
    const country = pick(COUNTRIES);
    const stage = pick(STAGE_ORDER);
    const reason = pick(REASONS);
    out.push({
      ts: new Date(Date.now() - i * 60000).toISOString(),
      email: `user${i}@example.com`,
      success: false,
      reason_code: reason,
      reason_text: reason,
      paypal_approve_url: "",
      amount_due: 0,
      currency: "USD",
      country,
      stage_reached: stage,
      chain_id: `C2000${i}`,
    });
  }
  return out;
}

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

  useEffect(() => {
    const cur = useStore.getState();
    if (cur.samplesLoaded[sampleTab]) return;
    let cancelled = false;

    (async () => {
      let list: Sample[] = [];
      try {
        const data = await api(`/api/samples?success=${sampleTab === "success"}`);
        if (cancelled) return;
        if (data && data.ok && Array.isArray(data.samples)) {
          list = data.samples;
        }
      } catch {
        // 网络异常时回退到 mock
      }

      if (list.length === 0) {
        list =
          sampleTab === "success" ? genSuccessSamples(8) : genFailureSamples(8);
      }

      if (cancelled) return;
      const latest = useStore.getState();
      useStore.setState({
        samples: { ...latest.samples, [sampleTab]: list },
        samplesLoaded: { ...latest.samplesLoaded, [sampleTab]: true },
      });
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sampleTab]);

  const switchTab = (t: "success" | "failure") => {
    if (t !== sampleTab) setSampleTab(t);
  };

  const list: Sample[] = samples[sampleTab] || [];
  const loaded = samplesLoaded[sampleTab];

  return (
    <div className="page">
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
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
            <table className="table">
              <thead>
                <tr>
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
                      <tr key={s.chain_id || i}>
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
                      <tr key={s.chain_id || i}>
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
