import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Mailbox, MailPoolData, MailPoolRules, MailPoolStats, ImapPreset, AliasMode } from "../types";
import { CheckIcon, XIcon } from "../components/icons";

const EMPTY_MBOX: Omit<Mailbox, "id" | "created_at" | "status" | "last_check" | "last_error" | "used_count"> = {
  label: "",
  imap_host: "",
  imap_port: 993,
  imap_ssl: true,
  username: "",
  password: "",
  alias_mode: "direct",
  catchall_domain: "",
  sender_whitelist: [],
  subject_whitelist: [],
  code_regex: "",
  enabled: true,
};

export function MailPoolView() {
  const [data, setData] = useState<MailPoolData | null>(null);
  const [stats, setStats] = useState<MailPoolStats | null>(null);
  const [editing, setEditing] = useState<Partial<Mailbox> | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null); // null = 新建
  const [showPw, setShowPw] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");
  const [bulkResult, setBulkResult] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // 测试中 id
  const [msg, setMsg] = useState<string | null>(null);

  const rulesTimer = useRef<number | undefined>(undefined);
  const [rules, setRules] = useState<MailPoolRules>({ sender_whitelist: [], subject_whitelist: [], code_regex: "" });

  async function loadAll() {
    try {
      const d = await api<MailPoolData>("/api/mail_pool");
      setData(d);
      setRules(d.rules);
      const s = await api<MailPoolStats>("/api/mail_pool/stats");
      setStats(s);
    } catch (e: any) {
      setMsg("加载失败: " + (e?.message || e));
    }
  }

  useEffect(() => { loadAll(); }, []);

  // 规则 1s 防抖自动保存
  function scheduleRulesSave(next: MailPoolRules) {
    setRules(next);
    if (rulesTimer.current) window.clearTimeout(rulesTimer.current);
    rulesTimer.current = window.setTimeout(async () => {
      try { await api("/api/mail_pool/rules", "PUT", next); } catch (e: any) { setMsg("规则保存失败: " + (e?.message || e)); }
    }, 1000);
  }

  async function saveMailbox() {
    if (!editing) return;
    if (!editing.imap_host || !editing.username) { setMsg("主机和用户名必填"); return; }
    try {
      if (editingId) {
        await api(`/api/mail_pool/${editingId}`, "PUT", editing);
      } else {
        await api("/api/mail_pool", "POST", editing);
      }
      setEditing(null); setEditingId(null); setShowPw(false);
      await loadAll();
      setMsg(null);
    } catch (e: any) { setMsg("保存失败: " + (e?.message || e)); }
  }

  async function delMailbox(id: string) {
    if (!confirm("删除该邮箱？")) return;
    try { await api(`/api/mail_pool/${id}`, "DELETE"); await loadAll(); } catch (e: any) { setMsg("删除失败: " + (e?.message || e)); }
  }

  async function toggleEnabled(m: Mailbox) {
    try {
      await api(`/api/mail_pool/${m.id}/${m.enabled ? "disable" : "enable"}`, "POST");
      await loadAll();
    } catch (e: any) { setMsg("切换失败: " + (e?.message || e)); }
  }

  async function testOne(id: string) {
    setBusy(id);
    try {
      const r = await api<{ ok: boolean; status: string; last_error: string }>(`/api/mail_pool/${id}/test`, "POST");
      setMsg(r.ok ? "连接成功 <CheckIcon />" : `连接失败: ${r.last_error || ""}`);
      await loadAll();
    } catch (e: any) { setMsg("测试失败: " + (e?.message || e)); }
    finally { setBusy(null); }
  }

  async function testAll() {
    setBusy("__all__");
    setMsg("正在逐个测试...");
    try {
      const r = await api<{ ok: boolean; results: { id: string; ok: boolean; last_error: string }[] }>("/api/mail_pool/test_all", "POST");
      const ok = r.results.filter((x) => x.ok).length;
      setMsg(`全部测试完成: ${ok}/${r.results.length} 成功`);
      await loadAll();
    } catch (e: any) { setMsg("批量测试失败: " + (e?.message || e)); }
    finally { setBusy(null); }
  }

  /* ── 批量管理 ── */
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const mboxes = data?.mailboxes ?? [];
  const allSelected = mboxes.length > 0 && mboxes.every((m) => selected.has(m.id));
  const toggleSelect = (id: string) =>
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleSelectAll = () =>
    setSelected(allSelected ? new Set() : new Set(mboxes.map((m) => m.id)));

  async function bulkSetEnabled(enabled: boolean) {
    const ids = Array.from(selected);
    if (!ids.length) return;
    try {
      await api("/api/mail_pool/bulk_enable", "POST", { mbox_ids: ids, enabled });
      setSelected(new Set());
      await loadAll();
      setMsg(`已${enabled ? "启用" : "禁用"} ${ids.length} 个邮箱`);
    } catch (e: any) { setMsg("批量操作失败: " + (e?.message || e)); }
  }

  async function bulkTest() {
    const ids = Array.from(selected);
    if (!ids.length) return;
    setBusy("__all__");
    setMsg(`正在测试 ${ids.length} 个邮箱...`);
    try {
      const r = await api<{ ok: boolean; results: { id: string; ok: boolean; last_error: string }[] }>("/api/mail_pool/test_all", "POST", { ids });
      const ok = r.results.filter((x) => x.ok).length;
      setMsg(`批量测试完成: ${ok}/${r.results.length} 成功`);
      await loadAll();
    } catch (e: any) { setMsg("批量测试失败: " + (e?.message || e)); }
    finally { setBusy(null); }
  }

  async function bulkDelete() {
    const ids = Array.from(selected);
    if (!ids.length) return;
    if (!confirm(`确认删除选中的 ${ids.length} 个邮箱？此操作不可撤销。`)) return;
    try {
      await api("/api/mail_pool/bulk_delete", "POST", { mbox_ids: ids });
      setSelected(new Set());
      await loadAll();
      setMsg(`已删除 ${ids.length} 个邮箱`);
    } catch (e: any) { setMsg("批量删除失败: " + (e?.message || e)); }
  }

  async function bulkImport() {
    setBulkResult(null);
    try {
      const r = await api<{ ok: boolean; added: number; skipped: number; errors: string[] }>("/api/mail_pool/bulk", "POST", { text: bulkText });
      setBulkResult(`成功 ${r.added} 条, 跳过 ${r.skipped} 条${r.errors.length ? ` (${r.errors.slice(0, 3).join("; ")})` : ""}`);
      if (r.added > 0) { await loadAll(); setBulkText(""); }
    } catch (e: any) { setBulkResult("导入失败: " + (e?.message || e)); }
  }

  function startEdit(m: Mailbox) {
    setEditing({ ...m }); setEditingId(m.id); setShowPw(false);
  }
  function startAdd() {
    setEditing({ ...EMPTY_MBOX }); setEditingId(null); setShowPw(false);
  }
  function applyPreset(p: ImapPreset) {
    if (!editing) return;
    setEditing({ ...editing, imap_host: p.imap_host, imap_port: p.imap_port, imap_ssl: p.imap_ssl });
  }

  // ── 预设管理: 用户可增删自定义预设主机 (不再硬编码, 落盘 mail_presets.json) ──
  const [presetOpen, setPresetOpen] = useState(false);
  const [presetForm, setPresetForm] = useState<ImapPreset>({ label: "", imap_host: "", imap_port: 993, imap_ssl: true });

  async function addPreset() {
    const label = presetForm.label.trim();
    const host = presetForm.imap_host.trim();
    if (!label || !host) { setMsg("预设标签和主机不能为空"); return; }
    try {
      await api("/api/mail_pool/presets", "POST", {
        label, imap_host: host, imap_port: presetForm.imap_port || 993, imap_ssl: presetForm.imap_ssl,
      });
      setPresetForm({ label: "", imap_host: "", imap_port: 993, imap_ssl: true });
      setPresetOpen(false);
      await loadAll();
    } catch (e: any) { setMsg("添加预设失败: " + (e?.message || e)); }
  }
  async function delPreset(label: string) {
    if (!window.confirm(`删除预设「${label}」？`)) return;
    try {
      await api(`/api/mail_pool/presets/${encodeURIComponent(label)}`, "DELETE");
      await loadAll();
    } catch (e: any) { setMsg("删除预设失败: " + (e?.message || e)); }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">邮箱池</h2>
          <p className="page-sub">自定义 IMAP 邮箱接入 · 注册验证码自动收取 · 邮箱池批量管理</p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={loadAll}>刷新</button>
          <button className="btn" onClick={testAll} disabled={busy === "__all__" || !data?.mailboxes.length}>
            {busy === "__all__" ? "测试中..." : "全部测试"}
          </button>
          <button className="btn" onClick={() => setBulkOpen((v) => !v)}>批量导入</button>
          <button className="btn btn-primary" onClick={startAdd}>+ 添加邮箱</button>
        </div>
      </div>

      {msg && (
        <div style={{ marginBottom: 12, padding: "8px 12px", borderRadius: "var(--r-input)",
          background: "var(--info-soft)", color: "var(--fg-info)", fontSize: 13 }}>
          {msg}
        </div>
      )}

      {/* 统计卡 */}
      <div className="stat-grid" style={{ marginBottom: 14 }}>
        <div className="stat-card"><div className="stat-label">邮箱总数</div><div className="stat-value">{stats?.total ?? "—"}</div><div className="stat-foot">全部</div></div>
        <div className="stat-card"><div className="stat-label">可用</div><div className="stat-value" style={{ color: "var(--ok)" }}>{stats?.enabled ?? "—"}</div><div className="stat-foot">已启用</div></div>
        <div className="stat-card"><div className="stat-label">连接正常</div><div className="stat-value" style={{ color: "var(--ok)" }}>{stats?.ok_count ?? "—"}</div><div className="stat-foot">测试通过</div></div>
        <div className="stat-card"><div className="stat-label">连接失败</div><div className="stat-value" style={{ color: "var(--danger)" }}>{stats?.fail ?? "—"}</div><div className="stat-foot">需检查凭据</div></div>
        <div className="stat-card"><div className="stat-label">已用次数</div><div className="stat-value">{stats?.used_total ?? "—"}</div><div className="stat-foot">direct 领用累计</div></div>
      </div>

      {/* 全局取码规则 */}
      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head"><span className="card-title">取码规则（全局默认，单邮箱可覆盖）</span></div>
        <div className="card-body">
          <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
            <label className="field">
              <span className="field-label">发件人白名单</span>
              <input className="input" placeholder="openai.com, noreply, auth0"
                value={rules.sender_whitelist.join(", ")}
                onChange={(e) => scheduleRulesSave({ ...rules, sender_whitelist: splitCsv(e.target.value) })} />
              <span className="field-hint">逗号分隔，发件人含其一即通过</span>
            </label>
            <label className="field">
              <span className="field-label">主题白名单</span>
              <input className="input" placeholder="verification, verify, code"
                value={rules.subject_whitelist.join(", ")}
                onChange={(e) => scheduleRulesSave({ ...rules, subject_whitelist: splitCsv(e.target.value) })} />
              <span className="field-hint">逗号分隔，主题含其一即通过</span>
            </label>
            <label className="field">
              <span className="field-label">验证码正则</span>
              <input className="input" placeholder="\b(\d{4,8})\b" value={rules.code_regex}
                onChange={(e) => scheduleRulesSave({ ...rules, code_regex: e.target.value })} />
              <span className="field-hint">第一个捕获组即验证码</span>
            </label>
          </div>
        </div>
      </section>

      {/* 批量导入 */}
      {bulkOpen && (
        <section className="card" style={{ marginBottom: 14 }}>
          <div className="card-head"><span className="card-title">批量导入</span></div>
          <div className="card-body">
            <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>每行格式：<code>imap_host|port|username|password|alias_mode|catchall_domain</code>（alias_mode: direct/catchall；至少 4 段）</p>
            <textarea className="input" rows={5} style={{ fontFamily: "var(--font-mono)" }} placeholder="imap.gmail.com|993|user@gmail.com|apppwd|catchall|@dom.com"
              value={bulkText} onChange={(e) => setBulkText(e.target.value)} />
            <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
              <button className="btn btn-primary" onClick={bulkImport} disabled={!bulkText.trim()}>导入</button>
              <button className="btn" onClick={() => { setBulkOpen(false); setBulkText(""); setBulkResult(null); }}>关闭</button>
              {bulkResult && <span className="muted" style={{ alignSelf: "center", fontSize: 12 }}>{bulkResult}</span>}
            </div>
          </div>
        </section>
      )}

      {/* 邮箱列表 */}
      <section className="card">
        <div className="card-head"><span className="card-title">邮箱列表 ({data?.mailboxes.length ?? 0})</span></div>
        <div className="card-body" style={{ padding: 0 }}>
          {!data?.mailboxes.length ? (
            <div className="empty" style={{ padding: 32, textAlign: "center" }}>
              <p className="muted">暂无邮箱。点击「+ 添加邮箱」或「批量导入」开始。</p>
              <p className="muted" style={{ fontSize: 12 }}>注册页选择 imap 渠道即可从此池领用邮箱收取验证码。</p>
            </div>
          ) : (
            <>
            {selected.size > 0 && (
              <div className="batch-bar">
                <span className="tag">已选 {selected.size}</span>
                <button className="btn btn-sm" onClick={() => bulkSetEnabled(true)}>批量启用</button>
                <button className="btn btn-sm" onClick={() => bulkSetEnabled(false)}>批量禁用</button>
                <button className="btn btn-sm" onClick={bulkTest} disabled={busy === "__all__"}>{busy === "__all__" ? "测试中..." : "批量测试"}</button>
                <button className="btn btn-sm btn-danger" onClick={bulkDelete}>批量删除</button>
                <button className="btn btn-sm btn-ghost" onClick={() => setSelected(new Set())}>取消选择</button>
              </div>
            )}
            <table className="table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th style={{ width: 32 }}><input type="checkbox" checked={allSelected} onChange={toggleSelectAll} /></th>
                  <th style={{ textAlign: "left" }}>标签 / 主机</th>
                  <th>用户名</th>
                  <th>地址模式</th>
                  <th>状态</th>
                  <th>已用</th>
                  <th>最近检查</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {data!.mailboxes.map((m) => (
                  <tr key={m.id} className={selected.has(m.id) ? "row-selected" : ""}>
                    <td><input type="checkbox" checked={selected.has(m.id)} onChange={() => toggleSelect(m.id)} /></td>
                    <td style={{ textAlign: "left" }}>
                      <div className="cell-strong">{m.label || "—"}</div>
                      <div className="cell-sub">{m.imap_host}:{m.imap_port}{m.imap_ssl ? "" : " (明文)"}</div>
                    </td>
                    <td><span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{m.username}</span></td>
                    <td>{m.alias_mode === "catchall" ? <span className="badge badge-info">catch-all{m.catchall_domain ? ` ${m.catchall_domain}` : ""}</span> : <span className="badge badge-muted">原地址</span>}</td>
                    <td>{<StatusBadge status={m.status} enabled={m.enabled} />}</td>
                    <td>{m.used_count}</td>
                    <td className="muted" style={{ fontSize: 11 }}>{m.last_check ? m.last_check.slice(5, 16).replace("T", " ") : "—"}</td>
                    <td>
                      <div style={{ display: "flex", gap: 4, justifyContent: "center" }}>
                        <button className="btn btn-sm" onClick={() => testOne(m.id)} disabled={busy === m.id}>{busy === m.id ? "..." : "测试"}</button>
                        <button className="btn btn-sm btn-ghost" onClick={() => toggleEnabled(m)}>{m.enabled ? "禁用" : "启用"}</button>
                        <button className="btn btn-sm btn-ghost" onClick={() => startEdit(m)}>编辑</button>
                        <button className="btn btn-sm btn-ghost" style={{ color: "var(--danger)" }} onClick={() => delMailbox(m.id)}>删</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </>
          )}
        </div>
      </section>

      {/* 添加/编辑 sheet */}
      {editing && (
        <>
          <div className="overlay" onClick={() => setEditing(null)} />
          <div className="sheet">
            <div className="sheet-head">
              <h3 className="sheet-title">{editingId ? "编辑邮箱" : "添加邮箱"}</h3>
              <button className="btn btn-sm btn-ghost" onClick={() => setEditing(null)}><XIcon /></button>
            </div>
            <div className="sheet-body">
              {/* 预设主机（可增删, 不再硬编码） */}
              <div style={{ marginBottom: 12 }}>
                <span className="field-label">
                  预设主机（一键填充）
                  <button className="btn btn-sm btn-ghost" type="button" style={{ marginLeft: 8, fontSize: 11.5 }} onClick={() => setPresetOpen((v) => !v)}>{presetOpen ? "取消" : "+ 添加预设"}</button>
                </span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
                  {data?.presets.map((p) => (
                    <span key={p.label} style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
                      <button className="btn btn-sm" disabled={!p.imap_host} onClick={() => applyPreset(p)}>{p.label}</button>
                      <button className="btn btn-sm btn-ghost" type="button" title="删除预设" style={{ fontSize: 11, padding: "2px 6px" }} onClick={() => delPreset(p.label)}><XIcon /></button>
                    </span>
                  ))}
                </div>
                {presetOpen && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8, alignItems: "end" }}>
                    <label className="field" style={{ flex: "0 0 110px" }}>
                      <span className="field-label">标签</span>
                      <input className="input" value={presetForm.label} onChange={(e) => setPresetForm({ ...presetForm, label: e.target.value })} placeholder="Gmail" />
                    </label>
                    <label className="field" style={{ flex: "1 1 180px" }}>
                      <span className="field-label">IMAP 主机</span>
                      <input className="input" value={presetForm.imap_host} onChange={(e) => setPresetForm({ ...presetForm, imap_host: e.target.value })} placeholder="imap.gmail.com" />
                    </label>
                    <label className="field" style={{ flex: "0 0 90px" }}>
                      <span className="field-label">端口</span>
                      <input className="input" type="number" value={presetForm.imap_port} onChange={(e) => setPresetForm({ ...presetForm, imap_port: Number(e.target.value) || 993 })} />
                    </label>
                    <label className="field" style={{ flex: "0 0 70px" }}>
                      <span className="field-label">SSL</span>
                      <label className="switch" style={{ marginTop: 6 }}>
                        <input type="checkbox" checked={presetForm.imap_ssl} onChange={(e) => setPresetForm({ ...presetForm, imap_ssl: e.target.checked })} />
                        <span className="switch-track" />
                      </label>
                    </label>
                    <button className="btn btn-sm" type="button" onClick={addPreset}>保存</button>
                  </div>
                )}
              </div>
              <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <label className="field">
                  <span className="field-label">标签（可选）</span>
                  <input className="input" value={editing.label || ""} onChange={(e) => setEditing({ ...editing, label: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">IMAP 主机</span>
                  <input className="input" placeholder="imap.gmail.com" value={editing.imap_host || ""} onChange={(e) => setEditing({ ...editing, imap_host: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">端口</span>
                  <input className="input" type="number" value={editing.imap_port ?? 993} onChange={(e) => setEditing({ ...editing, imap_port: Number(e.target.value) || 993 })} />
                </label>
                <label className="field">
                  <span className="field-label">SSL</span>
                  <label className="switch" style={{ marginTop: 6 }}>
                    <input type="checkbox" checked={!!editing.imap_ssl} onChange={(e) => setEditing({ ...editing, imap_ssl: e.target.checked })} />
                    <span className="switch-track" />
                  </label>
                </label>
                <label className="field">
                  <span className="field-label">用户名（邮箱地址）</span>
                  <input className="input" placeholder="user@gmail.com" value={editing.username || ""} onChange={(e) => setEditing({ ...editing, username: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">密码 / 应用专用密码</span>
                  <div style={{ display: "flex", gap: 4 }}>
                    <input className="input" type={showPw ? "text" : "password"} placeholder="app-specific password" value={editing.password || ""}
                      onChange={(e) => setEditing({ ...editing, password: e.target.value })} />
                    <button className="btn btn-sm btn-ghost" type="button" onClick={() => setShowPw((v) => !v)}>{showPw ? "隐" : "显"}</button>
                  </div>
                  <span className="field-hint">Gmail/Outlook 需用应用专用密码，非账号密码</span>
                </label>
                <label className="field">
                  <span className="field-label">地址模式</span>
                  <select className="select" value={editing.alias_mode} onChange={(e) => setEditing({ ...editing, alias_mode: e.target.value as AliasMode })}>
                    <option value="direct">direct（用原地址注册）</option>
                    <option value="catchall">catchall（生成别名，共用收件箱）</option>
                  </select>
                </label>
                <label className="field">
                  <span className="field-label">catch-all 域（仅 catchall 模式）</span>
                  <input className="input" placeholder="@domain.com" value={editing.catchall_domain || ""} onChange={(e) => setEditing({ ...editing, catchall_domain: e.target.value })} />
                  <span className="field-hint">catchall 模式自动生成 oai+随机@该域</span>
                </label>
              </div>
              {/* 单邮箱覆盖规则 */}
              <div style={{ marginTop: 14, borderTop: "1px solid var(--border-faint)", paddingTop: 12 }}>
                <span className="field-label">覆盖取码规则（留空用全局默认）</span>
                <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr", marginTop: 8 }}>
                  <label className="field">
                    <span className="field-label">发件人白名单</span>
                    <input className="input" placeholder="留空=用全局" value={(editing.sender_whitelist || []).join(", ")} onChange={(e) => setEditing({ ...editing, sender_whitelist: splitCsv(e.target.value) })} />
                  </label>
                  <label className="field">
                    <span className="field-label">主题白名单</span>
                    <input className="input" placeholder="留空=用全局" value={(editing.subject_whitelist || []).join(", ")} onChange={(e) => setEditing({ ...editing, subject_whitelist: splitCsv(e.target.value) })} />
                  </label>
                  <label className="field">
                    <span className="field-label">验证码正则</span>
                    <input className="input" placeholder="留空=用全局" value={editing.code_regex || ""} onChange={(e) => setEditing({ ...editing, code_regex: e.target.value })} />
                  </label>
                </div>
              </div>
            </div>
            <div className="sheet-foot">
              <button className="btn" onClick={() => setEditing(null)}>取消</button>
              <button className="btn btn-primary" onClick={saveMailbox}>保存</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ── helpers ──────────────────────────────────────────────────── */
function splitCsv(s: string): string[] {
  return s.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
}

function StatusBadge({ status, enabled }: { status: Mailbox["status"]; enabled: boolean }) {
  if (!enabled) return <span className="badge badge-muted">已禁用</span>;
  if (status === "ok") return <span className="badge badge-success">● 正常</span>;
  if (status === "fail") return <span className="badge badge-danger">● 失败</span>;
  return <span className="badge badge-muted">○ 未测</span>;
}
