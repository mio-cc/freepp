import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import { BRANCH_CN } from "../types";
import type { Token, BranchName } from "../types";
import { CheckIcon, XIcon, FileIcon, BoltIcon, InboxIcon } from "../components/icons";

/* ==========================================================================
   Token 库 — 库存邮箱 + 提链启动入口
   - 按提链分支隔离 token 库 (source)
   - 批量提链 / 单行提链 / 重提 (成功、失败均可重提)
   - 状态区分: 未提链 / 提链中 / 已提链 / 失败 / 冷却 / 失效
   - 套餐 / 注册方式 (导入时从 JWT 元数据解析, 探测接口后续接入)
   ========================================================================== */

/** 分支 -> token 库来源标签 (与后端 config branch.token_source 对应) */
const BRANCH_TOKEN_SOURCE: Record<string, string> = {
  paypal: "stripe",
  momo: "momo",
  grok: "grok",
  pix: "pix",
  ideal: "ideal",
  upi: "upi",
  kakao: "kakao",
  blik: "blik",
  twint: "twint",
  direct: "direct",
  register: "register",
};

/** 下拉选项: 提链分支 + 注册账号源 (source=register) */
const TOKEN_SOURCE_OPTIONS: { key: string; label: string; source: string }[] = [
  ...(Object.keys(BRANCH_CN) as BranchName[]).map((b) => ({
    key: b,
    label: `提链: ${BRANCH_CN[b]} (${BRANCH_TOKEN_SOURCE[b]})`,
    source: BRANCH_TOKEN_SOURCE[b],
  })),
  { key: "register", label: "注册账号 (register)", source: "register" },
];

function activeBranchTokenSource(b: string): string {
  return BRANCH_TOKEN_SOURCE[b] || "stripe";
}

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  idle: { label: "未提链", cls: "badge-muted" },
  running: { label: "提链中", cls: "badge-info" },
  success: { label: "已提链", cls: "badge-success" },
  failed: { label: "失败", cls: "badge-danger" },
  cooldown: { label: "冷却", cls: "badge-warn" },
  expired: { label: "失效", cls: "badge-danger" },
};

const STATUS_OPTIONS = Object.entries({
  all: "全部状态",
  idle: "未提链",
  running: "提链中",
  success: "已提链",
  failed: "失败",
  cooldown: "冷却",
  expired: "失效",
});

/** 本地解析 JWT payload (不验签, 用于导入失焦校准预览)
 *  JWS 3 段 (明文 payload) / JWE 5 段 (alg=dir 加密, payload 不可解, 标记 jwe) */
function jwtMeta(jwt: string): { email: string; sub: string; account_id: string; plan_type: string; jwe: boolean } | null {
  const parts = jwt.trim().split(".");
  if (parts.length < 3 || parts.length > 5) return null;
  const b64url = (s: string) => {
    const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
    return b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  };
  try {
    const header = JSON.parse(atob(b64url(parts[0])));
    if ((header.alg || "").toLowerCase() === "dir" || parts.length >= 4) {
      // JWE 加密 session token: 无明文字段
      return { email: "", sub: "", account_id: "", plan_type: "", jwe: true };
    }
    const payload = JSON.parse(atob(b64url(parts[1])));
    const auth = payload["https://api.openai.com/auth"] || {};
    const prof = payload["https://api.openai.com/profile"] || {};
    const email = (prof && typeof prof === "object" && prof.email) || payload.email || "";
    return {
      email: String(email || ""),
      sub: String(payload.sub || ""),
      account_id: String(auth.user_id || ""),
      plan_type: String(auth.chatgpt_plan_type || auth.plan || payload.plan || "free"),
      jwe: false,
    };
  } catch {
    return null;
  }
}

interface CalibItem {
  ok: boolean;
  email: string;
  plan: string;
  err: string;
}
interface CalibResult {
  total: number;
  ok: number;
  fail: number;
  items: CalibItem[];
  firstErr: string;
}

function methodLabel(m: string): string {
  if (m === "email") return "邮箱";
  if (m === "phone") return "手机";
  if (m === "google") return "Google";
  if (m === "apple") return "Apple";
  return m || "-";
}

export function TokensView() {
  const tokens = useStore((s) => s.tokens);
  const selectedTokenIds = useStore((s) => s.selectedTokenIds);
  const toggleTokenSelect = useStore((s) => s.toggleTokenSelect);
  const selectAllTokens = useStore((s) => s.selectAllTokens);
  const clearTokenSelection = useStore((s) => s.clearTokenSelection);
  const pushLog = useStore((s) => s.pushLog);
  const activeBranch = useStore((s) => s.activeBranch);
  const setActiveBranch = useStore((s) => s.setActiveBranch);
  const [sourceFilter, setSourceFilter] = useState<string>(() => BRANCH_TOKEN_SOURCE[activeBranch] || "stripe");

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [raw, setRaw] = useState("");
  const [result, setResult] = useState("");
  const [calib, setCalib] = useState<CalibResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [probingId, setProbingId] = useState("");
  const [editingTagsId, setEditingTagsId] = useState("");
  const [editingTagsVal, setEditingTagsVal] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [poolUrl, setPoolUrl] = useState("");
  const [poolResult, setPoolResult] = useState("");
  const [poolBusy, setPoolBusy] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const PAGE_SIZES = [10, 20, 50, 100];

  const tokenSource = (t: Token): string => (t as any).source || "stripe";

  /** 当前视图标签: register 源用 "注册账号", 否则用分支中文名 */
  const viewBranchLabel = sourceFilter === "register" ? "注册账号" : BRANCH_CN[activeBranch] || activeBranch;

  /** 防御: tags 可能为数组/字符串/undefined */
  const tagsOf = (t: any): string[] =>
    Array.isArray(t?.tags) ? t.tags : typeof t?.tags === "string" && t.tags ? t.tags.split(",").map((s: string) => s.trim()).filter(Boolean) : [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tokens.filter((t) => {
      if (tokenSource(t) !== sourceFilter) return false;
      if (statusFilter !== "all" && t.status !== statusFilter) return false;
      if (tagFilter && !tagsOf(t).includes(tagFilter)) return false;
      if (!q) return true;
      return (
        (t.email || "").toLowerCase().includes(q) ||
        (t.sub || "").toLowerCase().includes(q) ||
        (t.account_id || "").toLowerCase().includes(q)
      );
    });
  }, [tokens, search, statusFilter, sourceFilter, tagFilter]);

  const allTags = useMemo(() => {
    const set = new Set<string>();
    tokens.forEach((t) => {
      if (tokenSource(t) !== sourceFilter) return;
      tagsOf(t).forEach((tag) => set.add(tag));
    });
    return Array.from(set).sort();
  }, [tokens, sourceFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageTokens = useMemo(
    () => filtered.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filtered, safePage, pageSize]
  );

  useEffect(() => {
    setPage(1);
  }, [search, statusFilter, sourceFilter, pageSize, tagFilter]);

  const allSelected =
    pageTokens.length > 0 && pageTokens.every((t) => selectedTokenIds.has(t.id));

  const togglePageSelect = () => {
    if (allSelected) {
      pageTokens.forEach((t) => {
        if (selectedTokenIds.has(t.id)) toggleTokenSelect(t.id);
      });
    } else {
      pageTokens.forEach((t) => {
        if (!selectedTokenIds.has(t.id)) toggleTokenSelect(t.id);
      });
    }
  };

  const statBadges = useMemo(() => {
    const c: Record<string, number> = {};
    for (const t of tokens) {
      if (tokenSource(t) !== sourceFilter) continue;
      c[t.status || "idle"] = (c[t.status || "idle"] || 0) + 1;
    }
    return c;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tokens, sourceFilter]);

  const handleRefresh = async () => {
    setBusy(true);
    try {
      const r = await api("/api/tokens");
      if (r && Array.isArray(r.tokens)) {
        useStore.setState({ tokens: r.tokens });
        const cur = r.tokens.filter((t: Token) => tokenSource(t) === sourceFilter);
        setResult(`已刷新，共 ${r.tokens.length} 个 Token（${sourceFilter} 库 ${cur.length} 个）`);
      } else {
        setResult("刷新失败: 返回数据异常");
      }
    } catch (e) {
      setResult("刷新失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleRepair = async () => {
    setBusy(true);
    try {
      const r = await api("/api/tokens/repair", "POST", {});
      setResult(`元数据修复完成: 修正 ${r?.fixed ?? 0} 条 / 共 ${r?.total ?? 0} 条`);
      const tokensR = await api("/api/tokens");
      if (tokensR && Array.isArray(tokensR.tokens)) {
        useStore.setState({ tokens: tokensR.tokens });
      }
    } catch (e) {
      setResult("修复失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleProbe = async (t: Token) => {
    setProbingId(t.id);
    try {
      const r = await api(`/api/tokens/${t.id}/probe`, "POST", {});
      const stype = r?.session_type || "";
      const probe = r?.probe || {};
      useStore.setState((s) => ({
        tokens: s.tokens.map((x) => (x.id === t.id ? { ...x, session_type: stype, probe } : x)),
      }));
      const pe = probe.token_error || "";
      setResult(`探测完成: ${t.email || t.sub} → ${stype || "未知"}${pe ? ` · ${pe}` : ""}${probe.promo ? ` · 优惠:${probe.promo === "yes" ? "有" : probe.promo}` : ""}`);
    } catch (e) {
      setResult("探测失败: " + (e as Error).message);
    } finally {
      setProbingId("");
    }
  };

  const probeProgress = useStore((s) => s.probeProgress);

  const probingNow = probeProgress.total > 0 && probeProgress.done < probeProgress.total;
  const handleBatchProbe = async () => {
    const ids = filtered.map((t) => t.id);
    if (ids.length === 0) {
      setResult("当前筛选下没有可探测的 Token");
      return;
    }
    useStore.setState({ probeProgress: { done: 0, total: ids.length } });
    setResult(`批量探测启动: ${ids.length} 个 Token，每完成一条实时更新…`);
    try {
      const r = await api("/api/tokens/probe", "POST", { ids });
      if (r && r.ok) {
        pushLog(`批量探测启动: ${r.started ?? 0} 个`, "ok");
        setResult(`批量探测已启动 ${r.started ?? 0} 个 Token，每完成一条实时更新…`);
      } else {
        setResult("批量探测启动失败: " + (r?.error || "未知错误"));
        useStore.setState({ probeProgress: { done: 0, total: 0 } });
      }
    } catch (e) {
      setResult("批量探测启动失败: " + (e as Error).message);
      useStore.setState({ probeProgress: { done: 0, total: 0 } });
    }
  };

  /** 下拉选项: 现有标签 + 预设常用标签 */
  const PRESET_TAGS = ["促销", "无优惠", "已吊销", "已过期", "限流", "cs_live", "oaics", "Google", "邮箱", "手机"];
  const tagOptions = useMemo(() => {
    const seen = new Set<string>();
    const out: { value: string; preset: boolean }[] = [];
    allTags.forEach((t) => { if (!seen.has(t)) { seen.add(t); out.push({ value: t, preset: false }); } });
    PRESET_TAGS.forEach((t) => { if (!seen.has(t)) { seen.add(t); out.push({ value: t, preset: true }); } });
    return out;
  }, [allTags]);

  const saveTags = async (t: Token, raw: string) => {
    const tags = raw.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      const r = await api(`/api/tokens/${t.id}/tags`, "POST", { tags });
      if (r && r.ok) {
        useStore.setState((s) => ({
          tokens: s.tokens.map((x) => (x.id === t.id ? { ...x, tags: r.tags || [] } : x)),
        }));
      }
    } catch { /* ignore */ }
  };

  const sessionBadge = (st: string | undefined) => {
    if (!st) return { label: "未探测", cls: "badge-muted" };
    if (st === "cs_live") return { label: "cs_live", cls: "badge-success" };
    if (st === "oaics") return { label: "oaics", cls: "badge-accent" };
    if (st.startsWith("error")) return { label: st.slice(0, 18), cls: "badge-danger" };
    return { label: st, cls: "badge-muted" };
  };

  const tokenErrBadge = (pe: string | undefined) => {
    if (!pe) return null;
    if (pe.includes("吊销")) return { label: pe, cls: "badge-danger" };
    if (pe.includes("过期")) return { label: pe, cls: "badge-warn" };
    if (pe.includes("限流")) return { label: pe, cls: "badge-warn" };
    return { label: pe.slice(0, 14), cls: "badge-danger" };
  };

  const promoBadge = (promo: string | undefined) => {
    if (!promo) return null;
    if (promo === "yes") return { label: "优惠<CheckIcon />", cls: "badge-success" };
    if (promo === "no") return { label: "无优惠", cls: "badge-muted" };
    return { label: promo.slice(0, 14), cls: "badge-warn" };
  };

  /** 递归收集账号对象 (兼容 mail-otp-server 导出: 数组 / sub2api.accounts / codex·codexmanager.tokens) */
function collectTokens(o: any, out: CalibItem[]) {
  if (!o || typeof o !== "object") return;
  if (Array.isArray(o)) {
    o.forEach((x) => collectTokens(x, out));
    return;
  }
  const tokens = o.tokens && typeof o.tokens === "object" ? o.tokens : {};
  const creds = o.credentials && typeof o.credentials === "object" ? o.credentials : {};
  const user = o.user && typeof o.user === "object" ? o.user : {};
  const account = o.account && typeof o.account === "object" ? o.account : {};
  const meta = o.meta && typeof o.meta === "object" ? o.meta : {};
  const at = String(
    o.accessToken || o.access_token ||
    tokens.accessToken || tokens.access_token ||
    creds.accessToken || creds.access_token || ""
  ).trim();
  const st = String(o.sessionToken || o.session_token || creds.sessionToken || creds.session_token || "").trim();
  const email = String(o.email || user.email || account.email || meta.label || "");
  const parsed = at && jwtMeta(at);
  if (parsed) {
    out.push({
      ok: true,
      email: parsed.jwe ? "JWE 加密 session token" : email || parsed.email || at.slice(0, 20) + "…",
      plan: parsed.jwe ? "jwe" : parsed.plan_type,
      err: "",
    });
    return;
  }
  if (st && jwtMeta(st)) {
    // 仅 session token 的条目 (如单独 JWE)
    out.push({ ok: true, email: "仅 session token (无 access token)", plan: "jwe", err: "" });
    return;
  }
  for (const k of ["accounts", "tokens", "credentials"]) {
    if (o[k] && typeof o[k] === "object") collectTokens(o[k], out);
  }
}

  const calibrateText = (text: string) => {
    const items: CalibItem[] = [];
    // 整段 JSON (对象 / 数组 / sub2api / codex 等包装)
    try {
      const whole = JSON.parse(text.trim());
      if (whole && typeof whole === "object") {
        collectTokens(whole, items);
        if (items.length > 0) {
          const ok = items.filter((i) => i.ok).length;
          const fail = items.length - ok;
          setCalib({
            total: items.length,
            ok,
            fail,
            items,
            firstErr: items.find((i) => !i.ok)?.err || "",
          });
          return;
        }
      }
    } catch {
      /* 整段解析失败则逐行 */
    }
    const lines = text.split("\n");
    for (const line of lines) {
      const t = line.trim();
      if (!t) continue;
      if (t.startsWith("{") || t.startsWith("[")) {
        try {
          const o = JSON.parse(t);
          collectTokens(o, items);
          continue;
        } catch {
          // 行内 JSON 失败, 落到裸 JWT 判定
        }
      }
      const meta = jwtMeta(t);
      if (meta) {
        items.push({
          ok: true,
          email: meta.jwe ? "JWE 加密 session token" : meta.email || meta.sub.slice(0, 20) + "…",
          plan: meta.jwe ? "jwe" : meta.plan_type,
          err: "",
        });
      } else {
        items.push({ ok: false, email: "", plan: "", err: "非 JWT / 非 JSON" });
      }
    }
    const ok = items.filter((i) => i.ok).length;
    const fail = items.length - ok;
    setCalib({
      total: items.length,
      ok,
      fail,
      items,
      firstErr: items.find((i) => !i.ok)?.err || "",
    });
  };

  const handleCalibrate = () => calibrateText(raw);

  const [filesInfo, setFilesInfo] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [reading, setReading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  const readFiles = async (list: FileList | File[]) => {
    const arr = Array.from(list);
    if (arr.length === 0) return;
    setReading(true);
    try {
      const texts = await Promise.all(arr.map((f) => f.text()));
      const joined = texts.join("\n");
      setRaw(joined);
      setFilesInfo(arr.map((f) => f.name));
      setResult(`已读取 ${arr.length} 个文件, 共 ${joined.length} 字符, 正在校准…`);
      setCalib(null);
      // setRaw 异步生效, 用文本直接校准
      calibrateText(joined);
    } catch (e) {
      setResult("读取文件失败: " + (e as Error).message);
    } finally {
      setReading(false);
    }
  };

  const handleImport = async () => {
    if (!raw.trim()) {
      setResult("请粘贴 Token JSON");
      return;
    }
    setBusy(true);
    try {
      const r = await api("/api/tokens/import", "POST", {
        raw,
        source: sourceFilter,
      });
      const tokensR = await api("/api/tokens");
      if (tokensR && Array.isArray(tokensR.tokens)) {
        useStore.setState({ tokens: tokensR.tokens });
      }
      setResult(`导入完成: 成功 ${r.imported ?? 0}, 失败 ${r.failed ?? 0}, 库内共 ${tokensR?.tokens?.length ?? 0} 个`);
    } catch (e) {
      setResult("导入失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleClear = () => {
    setRaw("");
    setResult("");
  };

  const handlePoolImport = async () => {
    setPoolBusy(true);
    setPoolResult("拉取注册池中…");
    try {
      const r = await api("/api/tokens/import-from-pool", "POST", {
        base_url: poolUrl.trim() || undefined,
        source: sourceFilter,
      });
      if (r && r.ok) {
        setPoolResult(`拉取 ${r.total ?? 0}, 导入 ${r.imported ?? 0}, 去重跳过 ${r.skipped ?? 0}`);
        const tokensR = await api("/api/tokens");
        if (tokensR && Array.isArray(tokensR.tokens)) {
          useStore.setState({ tokens: tokensR.tokens });
        }
      } else {
        setPoolResult(r?.error || "导入失败");
      }
    } catch (e) {
      setPoolResult("异常: " + (e as Error).message);
    } finally {
      setPoolBusy(false);
    }
  };

  const runChain = async (ids: string[]) => {
    if (ids.length === 0) return;
    setBusy(true);
    try {
      const res = await api("/api/chain/batch", "POST", {
        token_ids: ids,
        branch: sourceFilter === "register" ? "paypal" : activeBranch,
      });
      const label = ids.length === 1
        ? (tokens.find((t) => t.id === ids[0])?.email || ids[0])
        : `${ids.length} 个 Token`;
      if (res && res.error) {
        pushLog(`${viewBranchLabel} 提链启动失败: ${res.error}`, "err");
        setResult(`启动失败: ${res.error}`);
      } else {
        pushLog(`${viewBranchLabel} 提链启动: ${label}`, "ok");
        setResult(`已启动 ${ids.length} 个 Token 的 ${viewBranchLabel} 提链`);
      }
    } catch (e) {
      pushLog(`${viewBranchLabel} 提链启动失败: ${(e as Error).message}`, "err");
      setResult("启动失败: 后端不可用");
    } finally {
      setBusy(false);
    }
  };

  const handleBatchStart = () => {
    const ids = Array.from(selectedTokenIds);
    if (ids.length === 0) {
      pushLog("请先勾选 Token", "warn");
      return;
    }
    runChain(ids);
  };

  const handleBulkDelete = async () => {
    const ids = Array.from(selectedTokenIds);
    if (ids.length === 0) return;
    if (!window.confirm(`确认删除选中的 ${ids.length} 个 Token？此操作不可撤销。`)) return;
    try {
      const r = await api<{ ok: boolean; deleted?: number; error?: string }>("/api/tokens/bulk_delete", "POST", { ids });
      if (r?.ok) {
        pushLog(`批量删除 ${r.deleted || 0} 个 Token`, "ok");
        clearTokenSelection();
        const tr = await api("/api/tokens");
        if (tr && Array.isArray(tr.tokens)) useStore.setState({ tokens: tr.tokens });
      } else {
        pushLog(`批量删除失败: ${r?.error || "未知"}`, "err");
      }
    } catch (e) {
      pushLog("批量删除失败: " + (e as Error).message, "err");
    }
  };

  const handleRunOne = (t: Token) => {
    runChain([t.id]);
  };

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h2 className="page-title">Token 库</h2>
          <p className="page-sub">
            库存邮箱 · {sourceFilter === "register" ? "注册账号" : `${viewBranchLabel} 提链入口`} · token 库 {sourceFilter}
          </p>
        </div>
        <div className="page-actions">
          <select
            className="select flex-field-sm"
            value={sourceFilter}
            onChange={(e) => {
              const v = e.target.value;
              setSourceFilter(v);
              clearTokenSelection();
            }}
          >
            {TOKEN_SOURCE_OPTIONS.map((o) => (
              <option key={o.key} value={o.source}>
                {o.label}
              </option>
            ))}
          </select>
          <input
            className="input flex-field"
            placeholder="搜索 email / sub / account_id"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            className="select flex-field-sm"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_OPTIONS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
          <select
            className="select flex-field-sm"
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
          >
            <option value="">全部标签</option>
            {tagOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.value}{opt.preset ? " <BoltIcon />" : ""}
              </option>
            ))}
          </select>
          {tagFilter && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setTagFilter("")}
              title="清除标签筛选"
            >
              清除<XIcon />
            </button>
          )}
          <button className="btn" onClick={handleRefresh} disabled={busy}>
            刷新
          </button>
          <button
            className="btn btn-ghost"
            onClick={handleRepair}
            disabled={busy}
            title="重算注册方式并清除被污染的邮箱 (user-xxx)"
          >
            重解析元数据
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">批量提链 — {viewBranchLabel}</span>
          <span className="card-hint">
            勾选几个就提几个 · 段重试/总尝试/账单国在「{viewBranchLabel}」链路配置页 · 已提链/失败的 Token 可重提
          </span>
        </div>
        <div className="card-body tight">
          <div className="inline-fields">
            <span className="badge badge-info">已选 {selectedTokenIds.size}</span>
            <span className="muted" style={{ fontSize: 12 }}>共 {filtered.length} 条</span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={togglePageSelect}
              disabled={pageTokens.length === 0}
            >
              {allSelected ? "取消本页全选" : "本页全选"}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={handleBatchProbe}
              disabled={probingNow || filtered.length === 0}
              title="探测当前筛选下全部 Token 的会话类型/优惠资格/token 状态"
            >
              {probingNow ? `探测中 ${probeProgress.done}/${probeProgress.total}` : `批量探测 (${filtered.length})`}
            </button>
            {probingNow && (
              <span className="badge badge-info" style={{ minWidth: 60 }}>
                {Math.round((probeProgress.done / probeProgress.total) * 100)}%
              </span>
            )}
            <button
              className="btn btn-primary"
              onClick={handleBatchStart}
              disabled={busy || selectedTokenIds.size === 0}
            >
              批量提链
            </button>
            <button
              className="btn btn-danger btn-sm"
              onClick={handleBulkDelete}
              disabled={selectedTokenIds.size === 0}
            >
              删除所选
            </button>
          </div>
          {result && (
            <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
              {result}
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-body tight" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {Object.entries(STATUS_BADGE).map(([st, cfg]) => {
            const n = statBadges[st] || 0;
            if (n === 0) return null;
            return (
              <span key={st} className={`badge ${cfg.cls}`}>
                {cfg.label} {n}
              </span>
            );
          })}
          {Object.keys(statBadges).length === 0 && (
            <span className="muted" style={{ fontSize: 12 }}>该分支库暂无 Token</span>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">导入 Token → {viewBranchLabel} 库</span>
          <span className="card-hint">
            粘贴 / 选择文件 / 选择文件夹 / 拖拽导入 · 自动解析全部 GPT 导出格式 (raw / session / cpa /
            sub2api / codex2api / codexmanager / cockpit / codex / JWT / JWE)
          </span>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files) readFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <input
          ref={dirInputRef}
          type="file"
          multiple
          {...({ webkitdirectory: "", directory: "" } as any)}
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files) readFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <div
          className={"import-dropzone" + (dragging ? " dropzone-active" : "")}
          style={{ padding: "14px 16px 0" }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            readFiles(e.dataTransfer.files);
          }}
        >
          <textarea
            className="textarea"
            rows={5}
            placeholder='{"accessToken":"...","sub":"..."} 或每行一个 JWT/JSON（失焦自动校准）· 或将文件拖到这里'
            value={raw}
            onChange={(e) => {
              setRaw(e.target.value);
              setCalib(null);
            }}
            onBlur={handleCalibrate}
          />
          {dragging && (
            <div className="dropzone-hint">
              <span style={{ fontSize: 18 }}><InboxIcon /></span> 松开鼠标导入文件…
            </div>
          )}
        </div>
        {filesInfo.length > 0 && (
          <div style={{ padding: "10px 16px 0", display: "flex", gap: 6, flexWrap: "wrap" }}>
            {filesInfo.slice(0, 10).map((n, i) => (
              <span key={i} className="tag file-item" style={{ animationDelay: `${i * 40}ms`, fontSize: 10.5 }}>
                {n}
              </span>
            ))}
            {filesInfo.length > 10 && (
              <span className="tag file-item" style={{ fontSize: 10.5 }}>+{filesInfo.length - 10} 个文件</span>
            )}
          </div>
        )}
        {calib && calib.total > 0 && (
          <div className="card-body tight" style={{ margin: "10px 16px 0", border: "1px solid var(--border-faint)" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <span className={`badge ${calib.ok > 0 ? "badge-success" : "badge-muted"}`}>
                有效 {calib.ok}
              </span>
              <span className="badge badge-muted">共 {calib.total} 行</span>
              {calib.fail > 0 && (
                <span className="badge badge-danger" title={calib.firstErr}>
                  无效 {calib.fail} · {calib.firstErr}
                </span>
              )}
              <span className="muted" style={{ fontSize: 11 }}>
                失焦校准 · 预览 (前 {Math.min(calib.items.length, 6)} 条):
              </span>
            </div>
            <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 2 }}>
              {calib.items.slice(0, 6).map((it, i) => (
                <div key={i} className="mono" style={{ fontSize: 11, display: "flex", gap: 8 }}>
                  <span style={{ color: it.ok ? "var(--ok)" : "var(--danger)" }}>
                    {it.ok ? "<CheckIcon />" : "<XIcon />"}
                  </span>
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {it.ok ? (it.email || "-") : it.err}
                  </span>
                  {it.ok && <span className="tag" style={{ fontSize: 10 }}>{it.plan}</span>}
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="btn-row">
          <button
            className="btn btn-primary"
            onClick={() => {
              handleCalibrate();
              handleImport();
            }}
            disabled={busy || reading}
          >
            {busy ? "导入中…" : "导入"}
          </button>
          <button className="btn" onClick={() => fileInputRef.current?.click()} disabled={busy || reading}>
            {reading ? "读取中…" : "<FileIcon /> 选择文件"}
          </button>
          <button className="btn" onClick={() => dirInputRef.current?.click()} disabled={busy || reading}>
            选择文件夹
          </button>
          <button
            className="btn"
            onClick={() => {
              handleClear();
              setCalib(null);
              setFilesInfo([]);
            }}
          >
            清空
          </button>
          {result && <span className="muted">{result}</span>}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">从注册池导入 → {viewBranchLabel} 库</span>
          <span className="card-hint">
            拉取 codex_register 未使用邮箱/token · access_token + email 双重去重
          </span>
        </div>
        <div className="btn-row" style={{ flexWrap: "wrap" }}>
          <input
            className="input"
            style={{ flex: 1, minWidth: 260 }}
            placeholder="注册池地址（默认 config.register_pool.base_url）"
            value={poolUrl}
            onChange={(e) => setPoolUrl(e.target.value)}
          />
          <button className="btn btn-primary" onClick={handlePoolImport} disabled={poolBusy}>
            {poolBusy ? "拉取中…" : "拉取并导入"}
          </button>
          {poolResult && <span className="muted">{poolResult}</span>}
        </div>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 34 }}>
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={togglePageSelect}
                />
              </th>
              <th>Email / Sub</th>
              <th>套餐</th>
              <th>注册方式</th>
              <th>探测</th>
              <th>标签</th>
              <th>上次运行</th>
              <th>状态</th>
              <th className="row-action" style={{ textAlign: "right" }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {pageTokens.length === 0 && (
              <tr>
                <td colSpan={9} className="muted" style={{ textAlign: "center" }}>
                  暂无数据 — 导入 Token 或切换提链分支
                </td>
              </tr>
            )}
            {pageTokens.map((t) => {
              const badge = STATUS_BADGE[t.status || "idle"] || STATUS_BADGE.idle;
              const isRunning = t.status === "running";
              const sbadge = sessionBadge((t as any).session_type);
              const probe = (t as any).probe || {};
              const terr = tokenErrBadge(probe.token_error);
              const pbadge = promoBadge(probe.promo);
              const paypal = probe.paypal ? "· paypal" : "";
              return (
                <tr
                  key={t.id}
                  className={selectedTokenIds.has(t.id) ? "row-selected" : ""}
                >
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedTokenIds.has(t.id)}
                      onChange={() => toggleTokenSelect(t.id)}
                      disabled={isRunning}
                    />
                  </td>
                  <td>
                    <div className="cell-strong">{t.email || "-"}</div>
                    <div className="cell-sub">{t.sub || t.account_id || "-"}</div>
                  </td>
                  <td>
                    <span className="tag">{t.plan_type || "free"}</span>
                  </td>
                  <td>{methodLabel(t.register_method)}</td>
                  <td>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, maxWidth: 150 }}>
                      <span className={`badge ${sbadge.cls}`} title={(t as any).session_type || "导入后自动探测 / 手动点击探测"}>
                        {sbadge.label}
                      </span>
                      {pbadge && <span className={`badge ${pbadge.cls}`} title="促销资格探测 (update 注入 promo)">{pbadge.label}</span>}
                      {terr && <span className={`badge ${terr.cls}`} title="checkout 返回的 token 状态">{terr.label}</span>}
                      {paypal && <span className="tag" title="init 显示 paypal 渠道可用">paypal<CheckIcon /></span>}
                    </div>
                  </td>
                  <td>
                    {editingTagsId === t.id ? (
                      <input
                        className="input"
                        style={{ width: 110 }}
                        autoFocus
                        value={editingTagsVal}
                        placeholder="逗号分隔"
                        onChange={(e) => setEditingTagsVal(e.target.value)}
                        onBlur={() => {
                          saveTags(t, editingTagsVal);
                          setEditingTagsId("");
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            saveTags(t, editingTagsVal);
                            setEditingTagsId("");
                          } else if (e.key === "Escape") {
                            setEditingTagsId("");
                          }
                        }}
                      />
                    ) : (
                      <div
                        style={{ display: "flex", flexWrap: "wrap", gap: 3, maxWidth: 150, cursor: "text" }}
                        onClick={() => {
                          setEditingTagsVal(tagsOf(t).join(", "));
                          setEditingTagsId(t.id);
                        }}
                        title="点击编辑标签 (逗号分隔)"
                      >
                        {tagsOf(t).length === 0 ? (
                          <span className="muted" style={{ fontSize: 11 }}>+ 标签</span>
                        ) : (
                          tagsOf(t).map((tag) => (
                            <span key={tag} className="tag" style={{ background: "var(--accent-dim)", color: "var(--accent)" }}>{tag}</span>
                          ))
                        )}
                      </div>
                    )}
                  </td>
                  <td className="muted" style={{ fontSize: 11 }}>
                    {t.last_run_at ? t.last_run_at.slice(0, 19).replace("T", " ") : "从未"}
                  </td>
                  <td>
                    <span className={`badge ${badge.cls}`}>{badge.label}</span>
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => handleProbe(t)}
                      disabled={busy || isRunning || probingId === t.id}
                      title="探测会话类型 / 优惠资格 / token 状态"
                    >
                      {probingId === t.id ? "探测中…" : "探测"}
                    </button>
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => handleRunOne(t)}
                      disabled={busy || isRunning}
                    >
                      {isRunning ? "提链中…" : t.status === "success" ? "重提" : t.status === "failed" ? "重提" : "提链"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="pager" style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12, flexWrap: "wrap" }}>
        <span className="muted" style={{ fontSize: 12 }}>
          {filtered.length === 0 ? "0 条" : `${(safePage - 1) * pageSize + 1}-${Math.min(safePage * pageSize, filtered.length)} / ${filtered.length} 条`}
        </span>
        <button
          className="btn btn-sm"
          onClick={() => setPage(safePage - 1)}
          disabled={safePage <= 1}
        >
          上一页
        </button>
        <span className="muted" style={{ fontSize: 12 }}>
          第 {safePage} / {totalPages} 页
        </span>
        <button
          className="btn btn-sm"
          onClick={() => setPage(safePage + 1)}
          disabled={safePage >= totalPages}
        >
          下一页
        </button>
        <select
          className="select select-sm"
          style={{ width: 90 }}
          value={pageSize}
          onChange={(e) => setPageSize(Number(e.target.value))}
        >
          {PAGE_SIZES.map((n) => (
            <option key={n} value={n}>{n} 条/页</option>
          ))}
        </select>
      </div>
    </div>
  );
}
