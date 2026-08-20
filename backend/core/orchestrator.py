"""并发调度器：AsyncChainOrchestrator + WebSocket 连接管理 + 统计聚合。

并发模型：
- asyncio 事件循环 + ThreadPoolExecutor(max_workers=thread_pool_size) 包装 curl_cffi 同步调用
- AsyncChainOrchestrator: Semaphore 限流 + asyncio.gather 批量执行
- 链路内并行: approve+poll 可并行, PM创建时预计算 confirm body (在 chain.py 内)

WebSocket: ConnectionManager 管理活跃连接，broadcast 推送实时链路状态。
统计: success/failure/byCountry/failByCountry/reasons/stageMatrix 实时聚合。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .chain import AsyncChain, ChainResult, DISPLAY_STAGES
from .config import settings
from .token_store import token_store


# =============================================================================
# WebSocket 连接管理器
# =============================================================================
class ConnectionManager:
    """管理 WebSocket 连接，支持广播事件。"""

    def __init__(self) -> None:
        self.active: list[asyncio.Queue] = []

    async def connect(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2048)
        self.active.append(q)
        return q

    def disconnect(self, q: asyncio.Queue) -> None:
        if q in self.active:
            self.active.remove(q)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """向所有连接广播事件。队列满时丢弃（避免阻塞）。"""
        dead: list[asyncio.Queue] = []
        for q in self.active:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.disconnect(q)

    async def send(self, q: asyncio.Queue, event: dict[str, Any]) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


# =============================================================================
# 全局统计聚合
# =============================================================================
class StatsAggregator:
    """累计统计：success/failure/byCountry/reasons/stageMatrix。"""

    def __init__(self) -> None:
        self.success: int = 0
        self.failure: int = 0
        self.byCountry: dict[str, int] = {}
        self.failByCountry: dict[str, int] = {}
        self.reasons: dict[str, int] = {}
        self.stageMatrix: dict[str, dict[str, dict[str, int]]] = {
            s: {} for s in DISPLAY_STAGES
        }
        self.latencies: list[float] = []

    def record_success(self, country: str, elapsed: float) -> None:
        self.success += 1
        self.byCountry[country] = self.byCountry.get(country, 0) + 1
        if elapsed > 0:
            self.latencies.append(round(elapsed, 2))
            if len(self.latencies) > 1000:
                self.latencies = self.latencies[-500:]

    def record_failure(self, country: str, reason_code: str) -> None:
        self.failure += 1
        if country:
            self.failByCountry[country] = self.failByCountry.get(country, 0) + 1
        if reason_code:
            self.reasons[reason_code] = self.reasons.get(reason_code, 0) + 1

    def record_stage(self, stage: str, country: str, ok: bool) -> None:
        if stage not in self.stageMatrix:
            self.stageMatrix[stage] = {}
        cell = self.stageMatrix[stage].setdefault(country, {"ok": 0, "fail": 0})
        cell["ok" if ok else "fail"] += 1

    def to_dict(self) -> dict[str, Any]:
        # 返回冻结快照: record_* 与 to_dict 并发 (广播 stats_update 时)
        # 会读到半更新的 dict; 浅拷贝顶层 + 深拷贝内层避免撕裂读。
        import copy as _copy
        return {
            "success": self.success,
            "failure": self.failure,
            "byCountry": dict(self.byCountry),
            "failByCountry": dict(self.failByCountry),
            "reasons": dict(self.reasons),
            "stageMatrix": _copy.deepcopy(self.stageMatrix),
        }

    def reset(self) -> None:
        self.success = 0
        self.failure = 0
        self.byCountry = {}
        self.failByCountry = {}
        self.reasons = {}
        self.stageMatrix = {s: {} for s in DISPLAY_STAGES}
        self.latencies = []


# =============================================================================
# 并发调度器
# =============================================================================
class AsyncChainOrchestrator:
    """并发链路调度器。

    - Semaphore 限流 max_concurrent
    - asyncio.gather 批量执行 (失败不中断)
    - 实时推送 chain_start/stage_*/chain_success/chain_failure/batch_progress
    """

    def __init__(self, conn_mgr: ConnectionManager) -> None:
        self.conn_mgr = conn_mgr
        self.stats = StatsAggregator()
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_chains)
        self.executor = ThreadPoolExecutor(max_workers=settings.thread_pool_size)
        self.active_chains: dict[str, AsyncChain] = {}
        self.chain_states: dict[str, dict[str, Any]] = {}
        self._batch_running = False
        self._batch_stop = False
        self._batch_task: asyncio.Task | None = None
        self._batch_start_time: float = 0.0
        self._batch_total = 0
        self._batch_done = 0

    # ------------------------------------------------------------------
    # 事件广播
    # ------------------------------------------------------------------
    async def emit(self, event: dict[str, Any]) -> None:
        """链路事件 -> 广播 + 更新内部状态。"""
        # 更新 chain_states
        cid = event.get("chain_id", "")
        etype = event.get("type", "")
        if etype == "chain_start" and cid:
            self.chain_states[cid] = {
                "stages": {}, "status": "running",
                "email": event.get("email", ""),
                "token_sub": event.get("token_sub", ""),
                "startTime": time.time() * 1000,
                "attempt": event.get("attempt", 1),
            }
        elif etype in ("stage_try", "stage_ok", "stage_fail") and cid:
            cs = self.chain_states.get(cid)
            if cs:
                stage = event.get("stage", "")
                state = {"run": "run", "ok": "ok", "fail": "fail"}.get(etype.replace("stage_", ""), "")
                if state:
                    cs["stages"][stage] = {
                        "state": state,
                        "country": event.get("country", ""),
                        "tryN": event.get("try_n", 1),
                        "maxTry": event.get("max_try", 3),
                    }
        elif etype == "chain_success" and cid:
            cs = self.chain_states.get(cid)
            if cs:
                cs["status"] = "success"
                cs["url"] = event.get("paypal_approve_url", "")
                # 冻结耗时: 终端状态后不再累计计时
                cs["endTime"] = time.time() * 1000
                cs["elapsed"] = round(float(event.get("elapsed") or 0) or
                                     (cs["endTime"] - cs.get("startTime", cs["endTime"])) / 1000, 2)
            self.stats.record_success(event.get("country", ""), event.get("elapsed", 0))
        elif etype == "chain_failure" and cid:
            cs = self.chain_states.get(cid)
            if cs:
                cs["status"] = "failed"
                cs["reason"] = event.get("reason_code", "")
                cs["endTime"] = time.time() * 1000
                cs["elapsed"] = round((cs["endTime"] - cs.get("startTime", cs["endTime"])) / 1000, 2)
            self.stats.record_failure(event.get("country", ""), event.get("reason_code", ""))

        # stage 矩阵记录
        if etype == "stage_ok" and cid:
            self.stats.record_stage(event.get("stage", ""), event.get("country", ""), True)
        elif etype == "stage_fail" and cid:
            self.stats.record_stage(event.get("stage", ""), event.get("country", ""), False)

        await self.conn_mgr.broadcast(event)

    # ------------------------------------------------------------------
    # 单链路执行 (含 attempts 重试)
    # ------------------------------------------------------------------
    async def run_chain(self, token: dict[str, Any], options: dict[str, Any]) -> ChainResult:
        attempts = int(options.get("attempts", 8))
        chain_id = uuid.uuid4().hex[:8]
        result = ChainResult()
        async with self.semaphore:
            for attempt in range(1, attempts + 1):
                if self._batch_stop:
                    result.reason_code = "network_error"
                    result.reason_text = "批量任务已停止"
                    break
                chain = AsyncChain(chain_id, token, attempt, options, self.emit, self.executor)
                self.active_chains[chain_id] = chain
                try:
                    result = await chain.execute()
                except asyncio.CancelledError:
                    result.reason_code = "network_error"
                    result.reason_text = "链路被取消"
                    break
                finally:
                    self.active_chains.pop(chain_id, None)
                if result.success:
                    break
                # 失败后短暂冷却再重试
                if attempt < attempts and not self._batch_stop:
                    await asyncio.sleep(0.5)
        # 更新 token 状态
        try:
            await token_store.set_status(token["id"], "success" if result.success else "failed")
            await self.conn_mgr.broadcast({
                "type": "token_status", "token_id": token["id"],
                "status": "success" if result.success else "failed",
            })
        except Exception:
            pass
        # 记录样本
        try:
            await token_store.add_sample(
                success=result.success, email=result.email, chain_id=chain_id,
                reason_code=result.reason_code, reason_text=result.reason_text,
                paypal_url=result.paypal_approve_url, amount_due=result.amount_due,
                currency=result.currency, country=result.country,
                stage_reached=result.stage_reached,
                payload=json.dumps({
                    "pm_authorize_url": result.pm_authorize_url,
                    "stage_geo": result.stage_geo,
                }, ensure_ascii=False),
                actual_country=result.actual_country,
                requested_country=result.requested_country,
                exit_ip=result.exit_ip,
                geo_confidence=result.geo_confidence,
            )
        except Exception:
            pass
        # 成功入库 (按分支渠道标记产出, 分支隔离)
        if result.success:
            try:
                branch_name = str(options.get("branch") or "paypal")
                channel = settings.branch(branch_name).channel
                # 授权段国家必须跟随 checkout 出口 IP 国家 (result.country),
                # 而非账单国 (billing_country) — PayPal 表单国家/指纹/接码按出口国对齐
                exit_cc = str(result.country or result.actual_country or "").upper()
                await token_store.add_success(
                    email=result.email, ba=result.ba_token,
                    paypal_url=result.paypal_approve_url, pm_url=result.pm_authorize_url,
                    amount_due=result.amount_due, currency=result.currency,
                    country=result.billing_country or exit_cc,
                    channel=channel,
                    exit_country=exit_cc,
                )
                # paypal 渠道: BA 自动进入授权队列, 并广播通知前端刷新
                if channel == "paypal" and result.paypal_approve_url:
                    from core.ba_queue import import_from_url
                    imported = import_from_url(
                        result.paypal_approve_url,
                        email=result.email,
                        country=exit_cc or result.billing_country,
                        chain_id=chain_id,
                    )
                    if imported:
                        await self.conn_mgr.broadcast({
                            "type": "ba_imported",
                            "ba_token": result.ba_token,
                            "email": result.email,
                        })
            except Exception:
                pass
        return result

    # ------------------------------------------------------------------
    # 批量执行
    # ------------------------------------------------------------------
    async def run_batch(self, token_ids: list[str], options: dict[str, Any]) -> dict[str, Any]:
        if self._batch_running:
            return {"ok": False, "error": "已有批量任务在运行"}
        # 加载 tokens
        tokens: list[dict[str, Any]] = []
        for tid in token_ids:
            t = await token_store.get_token(tid)
            if t:
                tokens.append(t)
        if not tokens:
            return {"ok": False, "error": "未找到有效 Token"}
        self._batch_running = True
        self._batch_stop = False
        self._batch_total = len(tokens)
        self._batch_done = 0
        self._batch_start_time = time.time()
        self.chain_states = {}
        # 通知前端: 新一轮开始, 清空上一轮残留进度
        await self.conn_mgr.broadcast({
            "type": "batch_start", "total": len(tokens), "running": True,
        })
        # 并发上限默认 = 选中条数 (不设限流)，仅当显式传 max_concurrent 时作限制
        max_conc = int(options.get("max_concurrent") or len(tokens) or 1)
        # 重建信号量: stop_batch 先 await 旧 _batch_task 收尾再清 _batch_running,
        # 故此处无 in-flight 链持有旧信号量, 重建不会让旧链脱离新限流。
        self.semaphore = asyncio.Semaphore(max(1, max_conc))

        async def _run_all() -> None:
            success = 0
            failure = 0

            async def _one(t: dict[str, Any]) -> None:
                nonlocal success, failure
                try:
                    await token_store.set_status(t["id"], "running")
                    await self.conn_mgr.broadcast(
                        {"type": "token_status", "token_id": t["id"], "status": "running"})
                    r = await self.run_chain(t, options)
                    if r.success:
                        success += 1
                    else:
                        failure += 1
                except Exception:
                    failure += 1
                finally:
                    self._batch_done += 1
                    await self.conn_mgr.broadcast({
                        "type": "batch_progress",
                        "done": self._batch_done, "total": self._batch_total,
                        "success": success, "failure": failure,
                    })
                    await self.conn_mgr.broadcast({"type": "stats_update", "stats": self.stats.to_dict()})

            # 信号量已在 run_chain 内，这里直接 gather
            tasks = [asyncio.create_task(_one(t)) for t in tokens]
            await asyncio.gather(*tasks, return_exceptions=True)

            elapsed = time.time() - self._batch_start_time
            await self.conn_mgr.broadcast({
                "type": "batch_done", "success": success, "failure": failure,
                "elapsed": round(elapsed, 2),
            })
            self._batch_running = False

        self._batch_task = asyncio.create_task(_run_all())
        return {"ok": True, "total": len(tokens), "max_concurrent": max_conc}

    async def stop_batch(self, chain_ids: list[str] | None = None) -> dict[str, Any]:
        self._batch_stop = True
        if chain_ids:
            for cid in chain_ids:
                ch = self.active_chains.get(cid)
                if ch:
                    ch.cancel()
        else:
            for ch in list(self.active_chains.values()):
                ch.cancel()
        # 停止时必须复位批量状态并通知前端, 否则 _run_all 被取消后
        # 收尾代码不执行, _batch_running 永久 True (前端"停止全部"一直红/可点)
        # 顺序: 先 await 旧 _batch_task 收尾 (让 in-flight 链退出 run_chain 的信号量块),
        # 再清 _batch_running — 否则旧轮在飞链仍持有旧信号量时新轮已重建,
        # 旧链脱离新限流 (审计 #3)。
        if self._batch_task:
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._batch_running = False
        await self.conn_mgr.broadcast({
            "type": "batch_done", "success": 0, "failure": 0,
            "elapsed": round(time.time() - self._batch_start_time, 2),
        })
        return {"ok": True, "stopped": len(self.active_chains)}

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        # 快照 chain_states: emit 与 status() 同事件循环, 但统计期间 chain_start/success
        # 可能插入新键致 dict changed size during iteration; 拷贝一份再聚合。
        states = list(self.chain_states.items())
        active = sum(1 for _, c in states if c.get("status") == "running")
        success = sum(1 for _, c in states if c.get("status") == "success")
        failed = sum(1 for _, c in states if c.get("status") == "failed")
        queued = max(0, self._batch_total - self._batch_done - active) if self._batch_running else 0
        return {
            "running": self._batch_running,
            "active": active,
            "queued": queued,
            "success": success,
            "failure": failed,
            "total": self._batch_total,
            "done": self._batch_done,
            "chains": [
                {"chain_id": cid, **c} for cid, c in states[:50]
            ],
        }

    def sync_payload(self) -> dict[str, Any]:
        """WebSocket sync 事件载荷。"""
        import copy as _copy
        traffic = {}
        try:
            from .proxy_pool import proxy_pool
            traffic = proxy_pool.get_traffic()
        except Exception:
            pass
        return {
            "type": "sync",
            # 深拷贝: 前端拿到的快照不应被后续 emit 就地修改 (撕裂读)。
            "chains": _copy.deepcopy(self.chain_states),
            "stats": self.stats.to_dict(),
            "running": self._batch_running,
            "latencies": list(self.stats.latencies[-100:]),
            "traffic": traffic,
        }

    async def shutdown(self) -> None:
        self._batch_stop = True
        for ch in list(self.active_chains.values()):
            ch.cancel()
        if self._batch_task:
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
        self.executor.shutdown(wait=False)
