import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

/* ==========================================================================
   直卡支付 — 绑卡 + 免税地址 + 订阅
   流程: 提链(HTTP) → CDP 绑卡 → 重新提链 → 免税地址 → 订阅
   ========================================================================== */

interface CardRecord {
  id: number;
  number: string;
  exp_month: string;
  exp_year: string;
  name: string;
  brand: string;
  uses: number;
  max_uses: number;
  note: string;
}

interface DpRecord {
  id: string;
  status: string;
  step: string;
  card_last4: string;
  taxfree_state: string;
  short_link: string;
  error: string;
}

const TAXFREE_OPTIONS = [
  { code: "DE", note: "首选 · 无州/地方销售税" },
  { code: "NH", note: "推荐 · 数字商品免税" },
  { code: "MT", note: "推荐 · 数字商品免税" },
  { code: "OR", note: "推荐 · 数字商品免税" },
  { code: "AK", note: "部分地方税 7.5%" },
];

export function DirectPayView() {
  const [tokenId, setTokenId] = useState("");
  const [cards, setCards] = useState<CardRecord[]>([]);
  const [records, setRecords] = useState<DpRecord[]>([]);
  const [taxfreeState, setTaxfreeState] = useState("DE");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState("");
  const [addr, setAddr] = useState<Record<string, string> | null>(null);
  const [pollId, setPollId] = useState("");

  /* 新卡表单 */
  const [newCard, setNewCard] = useState({ number: "", exp_month: "", exp_year: "", cvc: "", name: "" });

  const loadCards = useCallback(async () => {
    try {
      const d = await api("/api/directpay/cards");
      if (d && d.ok) setCards(d.cards || []);
    } catch { /* 静默 */ }
  }, []);

  const loadRecords = useCallback(async () => {
    try {
      const d = await api("/api/directpay/records");
      if (d && d.ok) setRecords(d.records || []);
    } catch { /* 静默 */ }
  }, []);

  useEffect(() => {
    loadCards();
    loadRecords();
  }, [loadCards, loadRecords]);

  /* 轮询 subscribe 结果 */
  useEffect(() => {
    if (!pollId) return;
    const timer = setInterval(async () => {
      const d = await api("/api/directpay/records");
      if (d && d.ok) {
        setRecords(d.records || []);
        const rec = (d.records || []).find((r: DpRecord) => r.id === pollId);
        if (rec && rec.status !== "running") {
          setPollId("");
          clearInterval(timer);
          setResult(
            rec.status === "success"
              ? `✅ 完成 — 短链: ${rec.short_link}`
              : `❌ 失败 (${rec.step}): ${rec.error}`
          );
        }
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [pollId]);

  const handleSubscribe = async () => {
    setLoading(true);
    setResult("");
    try {
      const d = await api("/api/directpay/subscribe", "POST", {
        token_id: tokenId || undefined,
        taxfree_state: taxfreeState,
        rebind_recheckout: true,
      });
      if (d && d.ok && d.record) {
        setPollId(d.record.id);
        setResult(`任务已启动: ${d.record.id}`);
      } else {
        setResult(`启动失败: ${(d as any)?.error || "未知"}`);
      }
    } catch {
      setResult("启动失败 (后端不可用)");
    } finally {
      setLoading(false);
    }
  };

  const handleAddCard = async () => {
    if (!newCard.number) return;
    setLoading(true);
    try {
      const d = await api("/api/directpay/cards", "POST", { ...newCard, max_uses: 10 });
      if (d && d.ok) {
        setNewCard({ number: "", exp_month: "", exp_year: "", cvc: "", name: "" });
        loadCards();
      }
    } catch { /* 静默 */ }
    finally { setLoading(false); }
  };

  const [cardSelected, setCardSelected] = useState<Set<number>>(new Set());
  const allCardsSelected = cards.length > 0 && cards.every((c) => cardSelected.has(c.id));
  const toggleCardSelect = (id: number) =>
    setCardSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAllCards = () =>
    setCardSelected(allCardsSelected ? new Set() : new Set(cards.map((c) => c.id)));
  const handleBulkDeleteCards = async () => {
    const ids = Array.from(cardSelected);
    if (ids.length === 0) return;
    if (!window.confirm(`确认删除选中的 ${ids.length} 张卡片？`)) return;
    try {
      const d = await api("/api/directpay/cards/bulk_delete", "POST", { card_ids: ids });
      if (d && d.ok) {
        setCardSelected(new Set());
        loadCards();
      }
    } catch { /* 静默 */ }
  };
  const handleDeleteCard = async (id: number) => {
    try { await api(`/api/directpay/cards/${id}`, "DELETE"); loadCards(); } catch { /* 静默 */ }
  };

  const handleGenAddr = async (state: string) => {
    const d = await api(`/api/directpay/taxfree?state=${state}`);
    if (d && d.ok) setAddr(d.address);
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">直卡支付</h2>
          <p className="page-sub">
            提链 → CDP 绑卡 → 重新提链 → 美国免税地址 → 订阅
          </p>
        </div>
      </div>

      <div className="grid grid-main">
        {/* 左: 订阅流程 */}
        <div className="card">
          <div className="card-head">
            <span className="card-title">订阅流程</span>
            <span className="card-hint">提链段在 Token 库完成 · 绑卡用 CDP 浏览器</span>
          </div>
          <div className="card-body">
            <div className="setting-row">
              <span className="setting-label">Token ID</span>
              <div className="setting-control">
                <input
                  className="input"
                  value={tokenId}
                  onChange={(e) => setTokenId(e.target.value)}
                  placeholder="留空用首个 token"
                />
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">免税州</span>
              <div className="setting-control">
                <select
                  className="select"
                  value={taxfreeState}
                  onChange={(e) => setTaxfreeState(e.target.value)}
                >
                  {TAXFREE_OPTIONS.map((o) => (
                    <option key={o.code} value={o.code}>{o.code} · {o.note}</option>
                  ))}
                </select>
                <button className="btn btn-sm" onClick={() => handleGenAddr(taxfreeState)}>
                  生成地址
                </button>
              </div>
            </div>
            {addr && (
              <div className="note" style={{ marginTop: 8 }}>
                免税地址: {addr.street}, {addr.city}, {addr.state} {addr.zip}
              </div>
            )}
            <button className="btn btn-primary" onClick={handleSubscribe} disabled={loading}>
              {loading ? "处理中…" : "开始订阅 (提链+绑卡+免税)"}
            </button>
            {result && <div className="note" style={{ marginTop: 8 }}>{result}</div>}
          </div>

          <div className="card-head" style={{ borderTop: "1px solid var(--border-faint)", marginTop: 8 }}>
            <span className="card-title">任务记录</span>
          </div>
          <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th><th>状态</th><th>步骤</th><th>卡尾号</th><th>免税州</th><th>短链</th>
                </tr>
              </thead>
              <tbody>
                {records.slice(-8).reverse().map((r) => (
                  <tr key={r.id}>
                    <td><code className="mono">{r.id.slice(0, 12)}</code></td>
                    <td>
                      <span className={`badge ${r.status === "success" ? "badge-success" : r.status === "failed" ? "badge-danger" : "badge-info"}`}>
                        {r.status}
                      </span>
                    </td>
                    <td>{r.step}</td>
                    <td>{r.card_last4 || "—"}</td>
                    <td>{r.taxfree_state}</td>
                    <td>
                      {r.short_link ? (
                        <a href={r.short_link} target="_blank" rel="noreferrer" style={{ fontSize: 11 }}>
                          {r.short_link.slice(0, 40)}…
                        </a>
                      ) : r.error ? <span style={{ color: "var(--danger)", fontSize: 11 }}>{r.error.slice(0, 40)}</span> : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* 右: 卡片库 */}
        <div className="card">
          <div className="card-head">
            <span className="card-title">卡片库</span>
            <span className="card-hint">绑卡用的卡信息 · 自动轮询</span>
          </div>
          <div className="card-body">
            <div className="grid grid-2">
              <input className="input" placeholder="卡号" value={newCard.number} onChange={(e) => setNewCard({ ...newCard, number: e.target.value })} />
              <input className="input" placeholder="月 (MM)" value={newCard.exp_month} onChange={(e) => setNewCard({ ...newCard, exp_month: e.target.value })} />
              <input className="input" placeholder="年 (YY)" value={newCard.exp_year} onChange={(e) => setNewCard({ ...newCard, exp_year: e.target.value })} />
              <input className="input" placeholder="CVV" value={newCard.cvc} onChange={(e) => setNewCard({ ...newCard, cvc: e.target.value })} />
              <input className="input" placeholder="持卡人" value={newCard.name} onChange={(e) => setNewCard({ ...newCard, name: e.target.value })} />
              <button className="btn btn-primary" onClick={handleAddCard} disabled={loading}>添加卡</button>
            </div>
          </div>
          <div className="table-wrap" style={{ border: "none", borderRadius: 0, borderTop: "1px solid var(--border-faint)" }}>
            {cardSelected.size > 0 && (
              <div className="batch-bar">
                <span className="tag">已选 {cardSelected.size}</span>
                <button className="btn btn-sm btn-danger" onClick={handleBulkDeleteCards}>删除所选</button>
                <button className="btn btn-sm btn-ghost" onClick={() => setCardSelected(new Set())}>取消选择</button>
              </div>
            )}
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}>
                    <input type="checkbox" checked={allCardsSelected} onChange={toggleAllCards} />
                  </th>
                  <th>卡号</th><th>有效期</th><th>持卡人</th><th>用量</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                {cards.length === 0 && (
                  <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--text-3)" }}>暂无卡片</td></tr>
                )}
                {cards.map((c) => (
                  <tr key={c.id} className={cardSelected.has(c.id) ? "row-selected" : ""}>
                    <td><input type="checkbox" checked={cardSelected.has(c.id)} onChange={() => toggleCardSelect(c.id)} /></td>
                    <td><code className="mono">{c.number}</code></td>
                    <td>{c.exp_month}/{c.exp_year}</td>
                    <td>{c.name || "—"}</td>
                    <td>{c.uses}/{c.max_uses}</td>
                    <td>
                      <button className="btn btn-sm btn-danger" onClick={() => handleDeleteCard(c.id)}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
