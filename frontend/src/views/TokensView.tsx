import { useMemo, useState } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import { BRANCH_CN } from "../types";
import type { Token, BranchName } from "../types";

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
};

function activeBranchTokenSource(b: BranchName): string {
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

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [raw, setRaw] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);

  const tokenSource = (t: Token): string => (t as any).source || "stripe";

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tokens.filter((t) => {
      if (tokenSource(t) !== activeBranchTokenSource(activeBranch)) return false;
      if (statusFilter !== "all" && t.status !== statusFilter) return false;
      if (!q) return true;
      return (
        (t.email || "").toLowerCase().includes(q) ||
        (t.sub || "").toLowerCase().includes(q) ||
        (t.account_id || "").toLowerCase().includes(q)
      );
    });
  }, [tokens, search, statusFilter, activeBranch]);

  const allSelected =
    filtered.length > 0 && filtered.every((t) => selectedTokenIds.has(t.id));

  const statBadges = useMemo(() => {
    const c: Record<string, number> = {};
    for (const t of tokens) {
      if (tokenSource(t) !== activeBranchTokenSource(activeBranch)) continue;
      c[t.status || "idle"] = (c[t.status || "idle"] || 0) + 1;
    }
    return c;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tokens, activeBranch]);

  const handleRefresh = async () => {
    setBusy(true);
    try {
      const r = await api(`/api/tokens?source=${activeBranchTokenSource(activeBranch)}`);
      if (r && Array.isArray(r.tokens)) {
        useStore.setState({ tokens: r.tokens });
        setResult(`已刷新，共 ${r.tokens.length} 个 Token`);
      } else {
        setResult("刷新失败: 返回数据异常");
      }
    } catch (e) {
      setResult("刷新失败: " + (e as Error).message);
    } finally {
      setBusy(false);
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
        source: activeBranchTokenSource(activeBranch),
      });
      if (r && Array.isArray(r.tokens)) {
        useStore.setState({ tokens: r.tokens });
      }
      setResult(`导入完成: 成功 ${r.imported ?? 0}, 失败 ${r.failed ?? 0}`);
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

  const runChain = async (ids: string[]) => {
    if (ids.length === 0) return;
    setBusy(true);
    try {
      const res = await api("/api/chain/batch", "POST", {
        token_ids: ids,
        branch: activeBranch,
      });
      const label = ids.length === 1
        ? (tokens.find((t) => t.id === ids[0])?.email || ids[0])
        : `${ids.length} 个 Token`;
      if (res && res.error) {
        pushLog(`${BRANCH_CN[activeBranch]} 提链启动失败: ${res.error}`, "err");
        setResult(`启动失败: ${res.error}`);
      } else {
        pushLog(`${BRANCH_CN[activeBranch]} 提链启动: ${label}`, "ok");
        setResult(`已启动 ${ids.length} 个 Token 的 ${BRANCH_CN[activeBranch]} 提链`);
      }
    } catch (e) {
      pushLog(`${BRANCH_CN[activeBranch]} 提链启动失败: ${(e as Error).message}`, "err");
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

  const handleRunOne = (t: Token) => {
    runChain([t.id]);
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">Token 库</h2>
          <p className="page-sub">
            库存邮箱 · {BRANCH_CN[activeBranch]} 提链入口 · token 库 {activeBranchTokenSource(activeBranch)}
          </p>
        </div>
        <div className="page-actions">
          <select
            className="select"
            style={{ width: 170 }}
            value={activeBranch}
            onChange={(e) => {
              setActiveBranch(e.target.value as BranchName);
              clearTokenSelection();
            }}
          >
            {(Object.keys(BRANCH_CN) as BranchName[]).map((b) => (
              <option key={b} value={b}>
                提链: {BRANCH_CN[b]} ({BRANCH_TOKEN_SOURCE[b]})
              </option>
            ))}
          </select>
          <input
            className="input"
            style={{ width: 200 }}
            placeholder="搜索 email / sub / account_id"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            className="select"
            style={{ width: 110 }}
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_OPTIONS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
          <button className="btn" onClick={handleRefresh} disabled={busy}>
            刷新
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">批量提链 — {BRANCH_CN[activeBranch]}</span>
          <span className="card-hint">
            勾选几个就提几个 · 段重试/总尝试/账单国在「{BRANCH_CN[activeBranch]}」链路配置页 · 已提链/失败的 Token 可重提
          </span>
        </div>
        <div className="card-body tight">
          <div className="inline-fields">
            <span className="badge badge-info">已选 {selectedTokenIds.size}</span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => selectAllTokens()}
              disabled={filtered.length === 0}
            >
              {allSelected ? "取消全选" : "全选"}
            </button>
            <button
              className="btn btn-primary"
              onClick={handleBatchStart}
              disabled={busy || selectedTokenIds.size === 0}
            >
              批量提链
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
          <span className="card-title">导入 Token → {BRANCH_CN[activeBranch]} 库</span>
          <span className="card-hint">
            粘贴 accessToken / Session JSON · 自动解析套餐与邮箱元数据
          </span>
        </div>
        <div style={{ padding: "14px 16px 0" }}>
          <textarea
            className="textarea"
            rows={5}
            placeholder='{"accessToken":"...","sub":"..."} 或每行一个 JSON'
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
          />
        </div>
        <div className="btn-row">
          <button className="btn btn-primary" onClick={handleImport} disabled={busy}>
            导入
          </button>
          <button className="btn" onClick={handleClear}>
            清空
          </button>
          {result && <span className="muted">{result}</span>}
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
                  onChange={() => selectAllTokens()}
                />
              </th>
              <th>Email / Sub</th>
              <th>套餐</th>
              <th>注册方式</th>
              <th>上次运行</th>
              <th>状态</th>
              <th className="row-action" style={{ textAlign: "right" }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={7} className="muted" style={{ textAlign: "center" }}>
                  暂无数据 — 导入 Token 或切换提链分支
                </td>
              </tr>
            )}
            {filtered.map((t) => {
              const badge = STATUS_BADGE[t.status || "idle"] || STATUS_BADGE.idle;
              const isRunning = t.status === "running";
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
                  <td className="muted" style={{ fontSize: 11 }}>
                    {t.last_run_at ? t.last_run_at.slice(0, 19).replace("T", " ") : "从未"}
                  </td>
                  <td>
                    <span className={`badge ${badge.cls}`}>{badge.label}</span>
                  </td>
                  <td style={{ textAlign: "right" }}>
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
    </div>
  );
}
