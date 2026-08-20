import { useEffect, useMemo, useState } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import { STAGE_ORDER, STAGE_SHORT, STAGE_CN } from "../types";
import { ChainTable } from "../components/chain/ChainTable";
import { SuccessSheet } from "../components/chain/SuccessSheet";

interface SheetState {
  url: string;
  meta: string;
}

export function ChainsView() {
  const chainStates = useStore((s) => s.chainStates);
  const batchTotal = useStore((s) => s.batchTotal);
  const batchDone = useStore((s) => s.batchDone);
  const batchRunning = useStore((s) => s.batchRunning);
  const pushLog = useStore((s) => s.pushLog);

  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const [busy, setBusy] = useState(false);
  const [sheet, setSheet] = useState<SheetState | null>(null);

  const chainList = useMemo(
    () => Object.entries(chainStates).map(([id, cs]) => ({ id, cs })),
    [chainStates]
  );

  const activeCount = chainList.filter(({ cs }) => cs.status === "running").length;
  const successCount = chainList.filter(({ cs }) => cs.status === "success").length;
  const failedCount = chainList.filter(({ cs }) => cs.status === "failed").length;
  const queuedCount = Math.max(0, batchTotal - batchDone - activeCount);
  /** 有运行中链路或有批次在排队时, 停止按钮进入可用的活跃态 */
  const hasActivity = activeCount > 0 || batchRunning || queuedCount > 0;

  const handleStop = async () => {
    setBusy(true);
    try {
      await api("/api/chain/stop", "POST", {});
      pushLog("已发送停止信号", "info");
    } catch (e) {
      pushLog(`停止失败: ${e}`, "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h2 className="page-title">链路监控</h2>
          <p className="page-sub">每条链路的 7 段管道进度、出口国家与耗时</p>
        </div>
        <div className="page-actions">
          {hasActivity ? (
            <button
              className="btn btn-danger btn-stop-live"
              onClick={handleStop}
              disabled={busy}
              title={`停止全部: ${activeCount} 条运行中, ${queuedCount} 条排队中`}
            >
              {busy ? "发送中…" : `■ 停止全部 (活跃 ${activeCount + queuedCount})`}
            </button>
          ) : (
            <button className="btn btn-ghost" disabled title="当前没有运行中的链路">
              停止全部
            </button>
          )}
        </div>
      </div>

      <div className="inline-fields" style={{ marginBottom: 14 }}>
        <span className="badge badge-info">活跃 {activeCount}</span>
        <span className="badge badge-success">成功 {successCount}</span>
        <span className="badge badge-danger">失败 {failedCount}</span>
        <span className="badge badge-muted">队列 {queuedCount}</span>
      </div>

      {chainList.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon">🔗</div>
            <div className="empty-title">尚未启动链路</div>
            <div className="empty-hint">到各链路页选择 Token 后启动提链</div>
          </div>
        </div>
      ) : (
        <ChainTable chainList={chainList} onClick={(url, meta) => setSheet({ url, meta })} />
      )}

      {sheet && (
        <SuccessSheet
          url={sheet.url}
          meta={sheet.meta}
          onClose={() => setSheet(null)}
        />
      )}
    </div>
  );
}
