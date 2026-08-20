# -*- coding: utf-8 -*-
"""core/pipeline_daemon.py — 一键流程守护引擎

  开启后后台自动跑完整链路: 注册 → 提链 → 支付授权, 三段并行流水线。

  架构:
    一个 asyncio 守护循环, 每 tick_interval 秒检查一次三段状态并按需触发。
    三段全部复用现有 API/函数, 不重写底层逻辑:
      - 注册: reg_engine.stream_registration (daemon 线程, STATE._running 防并发)
      - 提链: orchestrator.run_batch (asyncio.create_task, _batch_running 防并发)
      - 支付: ba_queue + _run_authorize_task (ba_try_start + _ba_acquire_slot 防并发)

  三段自身的原子守卫是最终防线; 守护循环触发失败就静默跳过, 下次再试, 不崩溃。
  stop() 只停守护循环, 不杀已运行任务 (cooperative drain), 复用各段原有 cooperative-stop 语义。
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "pipeline_config.json")
_CONFIG_FILE = os.path.normpath(_CONFIG_FILE)

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "unlimited": True,           # True: 无限跑; False: 达到 target_accounts 自动停
    "target_accounts": 100,      # unlimited=False 时的目标产账号数
    "tick_interval": 5,          # 守护循环轮询间隔 (秒)
    # 注册段
    "reg_batch_size": 10,        # 每批注册数量
    "reg_email_mode": "",  # 邮箱渠道 (空=自动取第一个 imap:<标签>)
    "reg_cooldown": 30,          # 注册冷却 (秒)
    "reg_proxy": "",             # 注册代理 (空=自动 711 sticky)
    "reg_country": "auto",       # 注册出口国家 (auto=随机; 指定国家码则用 711 按国构造)
    # 提链段
    "chain_batch_size": 5,       # 每批提链账号数
    "chain_concurrent": 3,       # 提链并发上限
    "chain_branch": "paypal",    # 提链分支
    "chain_attempts": 8,         # 单账号提链尝试次数
    "chain_partial_ok": False,   # False: 攒齐一批再跑; True: 不足一批也跑
    # 支付段
    "pay_max_concurrent": 3,     # 支付授权并发上限 (叠加于 _ba_config.max_concurrent)
}


def _load_config() -> dict[str, Any]:
    """原子加载 pipeline_config.json, 合并默认值。"""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update(saved)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg


def _save_config(cfg: dict[str, Any]) -> None:
    """原子写入 (tmp + os.replace, 复用 ba_config 模式)。"""
    tmp = _CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _CONFIG_FILE)


class PipelineDaemon:
    """一键流程守护单例。"""

    def __init__(self) -> None:
        self.enabled: bool = False
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.config: dict[str, Any] = _load_config()
        self._lock = threading.Lock()
        self.stats: dict[str, int] = {
            "reg_started": 0, "reg_success": 0,
            "chain_started": 0, "chain_success": 0,
            "pay_started": 0, "pay_success": 0,
        }
        self._last_tick: float = 0.0
        self._last_error: str = ""

    # ---- 生命周期 ----
    def start(self) -> None:
        """开启守护循环 (幂等)。"""
        self.enabled = True
        self.config["enabled"] = True
        _save_config(self.config)
        if self._task and not self._task.done():
            return
        self._stop.clear()
        try:
            self._task = asyncio.create_task(self._loop())
        except RuntimeError:
            # 无事件循环 (不应发生在 uvicorn lifespan 之后), 延迟创建
            self._task = None

    def stop(self) -> None:
        """关闭守护并停止已运行任务 (注册/提链/支付)。

        不仅退出守护循环, 还主动取消正在跑的提链批次与注册批次,
        让"停止守护"真正停下来而不是等已运行任务自然结束。
        """
        self.enabled = False
        self.config["enabled"] = False
        _save_config(self.config)
        self._stop.set()
        # 停止已运行的提链批次 (cancel 所有 active_chains + _batch_task)
        try:
            from api.deps import runtime
            if runtime and runtime.orchestrator:
                loop = getattr(runtime, "loop", None)
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        runtime.orchestrator.stop_batch(), loop)
        except Exception as e:
            print(f"[pipeline] stop 时取消提链批次异常(忽略): {repr(e)[:120]}")
        # 停止已运行的注册批次 (cancel_event 让注册循环在下一个检查点退出)
        try:
            from reg.engine import STATE as _reg_state
            _reg_state.request_cancel()
        except Exception as e:
            print(f"[pipeline] stop 时取消注册批次异常(忽略): {repr(e)[:120]}")

    def is_running(self) -> bool:
        return bool(self._task and not self._task.done() and self.enabled)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """合并配置更新并落盘, 立即生效。"""
        with self._lock:
            self.config.update(updates)
            _save_config(self.config)
        return dict(self.config)

    def status(self) -> dict[str, Any]:
        """返回守护状态快照 (供前端展示)。"""
        reg_running = False
        chain_running = False
        try:
            from reg.engine import STATE as _reg_state
            reg_running = _reg_state.is_running()
        except Exception:
            pass
        try:
            from api.deps import runtime
            if runtime and runtime.orchestrator:
                chain_running = bool(runtime.orchestrator._batch_running)
        except Exception:
            pass
        pay_running = 0
        try:
            from api.paypal import _ba_running_count
            pay_running = int(_ba_running_count)
        except Exception:
            pass
        return {
            "enabled": self.enabled,
            "running": self.is_running(),
            "config": dict(self.config),
            "stats": dict(self.stats),
            "stage_running": {
                "reg": reg_running,
                "chain": chain_running,
                "pay": pay_running > 0,
            },
            "pay_concurrent": pay_running,
            "last_tick": self._last_tick,
            "last_error": self._last_error,
        }

    # ---- 主循环 ----
    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.enabled:
                    await self._tick_register()
                    await self._tick_chain()
                    await self._tick_pay()
                    self._check_target()
                self._last_tick = time.time()
                self._last_error = ""
            except Exception as e:
                self._last_error = str(e)[:300]
                print(f"[pipeline] tick error: {e}")
            await asyncio.sleep(max(2, int(self.config.get("tick_interval", 5))))

    # ---- 注册段 ----
    async def _tick_register(self) -> None:
        """注册段: 空闲时触发一批, 产出账号供提链消费。"""
        from reg.engine import STATE as _reg_state, stream_registration
        if _reg_state.is_running():
            return  # 已在跑, 跳过
        try:
            from core.token_store import token_store
            idle = await token_store.count_idle(source="register")
        except Exception:
            idle = 0
        # 需求 = 目标 - (idle + 已注册在途). 无限模式下始终保持库存。
        target = int(self.config.get("target_accounts", 100))
        unlimited = bool(self.config.get("unlimited", True))
        if unlimited:
            # 无限模式: idle 低于 2 批就补 (保持流水线不断料)
            need = max(self.config["reg_batch_size"] - idle, 0)
        else:
            total_success = self.stats["reg_success"]
            need = target - (idle + total_success)
        if need <= 0:
            return
        batch = min(need, int(self.config.get("reg_batch_size", 10)))
        batch = max(1, batch)
        task_id = _reg_state.try_start()
        if not task_id:
            return  # 原子占位失败, 下次再试
        email_mode = str(self.config.get("reg_email_mode") or "").strip()
        if not email_mode:
            try:
                from reg.engine import email_channels as _ec
                _chans = list(_ec())
            except Exception:
                _chans = []
            if not _chans:
                print("[pipeline] 注册段跳过: 无可用邮箱渠道")
                _reg_state.request_cancel()  # 释放占位
                return
            email_mode = _chans[0]
            print(f"[pipeline] 注册段: 未指定渠道, 自动选用 {email_mode}")
        cooldown = float(self.config.get("reg_cooldown", 30))
        proxy = str(self.config.get("reg_proxy") or "").strip() or None
        country = str(self.config.get("reg_country") or "auto").strip().upper() or "AUTO"

        def _run():
            try:
                stream_registration(
                    count=batch, email_mode=email_mode, concurrency=1,
                    cooldown=cooldown, task_id=task_id, proxy=proxy, country=country)
            except Exception as e:
                print(f"[pipeline] reg batch error: {e}")

        threading.Thread(target=_run, name="pipeline-reg", daemon=True).start()
        self.stats["reg_started"] += batch
        print(f"[pipeline] 注册段触发: batch={batch} email_mode={email_mode} idle={idle}")

    # ---- 提链段 ----
    async def _tick_chain(self) -> None:
        """提链段: 提链空闲 + idle 账号充足时触发一批。"""
        from api.deps import runtime
        if not runtime or not runtime.orchestrator:
            return
        orch = runtime.orchestrator
        if orch._batch_running:
            return  # 提链已在跑, 跳过
        batch_size = int(self.config.get("chain_batch_size", 5))
        partial_ok = bool(self.config.get("chain_partial_ok", False))
        try:
            from core.token_store import token_store
            idle = await token_store.list_idle_tokens(source="register", limit=batch_size)
        except Exception:
            return
        if not idle:
            return
        if len(idle) < batch_size and not partial_ok:
            return  # 攒齐一批再跑
        token_ids = [t["id"] for t in idle[:batch_size]]
        branch = str(self.config.get("chain_branch") or "paypal")
        from core.config import settings
        bcfg = settings.branch(branch)
        options = {
            "retry_per_stage": 3,
            "attempts": int(self.config.get("chain_attempts", bcfg.attempts)),
            "auto_billing": True,
            "require_zero": True,
            "channel_check": True,
            "branch": branch,
            "max_concurrent": int(self.config.get("chain_concurrent", 3)),
        }
        result = await orch.run_batch(token_ids, options)
        if result.get("ok"):
            self.stats["chain_started"] += len(token_ids)
            print(f"[pipeline] 提链段触发: tokens={len(token_ids)} branch={branch}")

    # ---- 支付段 ----
    async def _tick_pay(self) -> None:
        """支付段: 扫描 pending BA + 空位, 逐条触发授权。"""
        from api.paypal import _run_authorize_task, _ba_acquire_slot, _ba_release_slot, _merged_ba_config
        from core.ba_queue import list_records, try_start as ba_try_start, update as ba_update
        pending = [r for r in list_records() if r.get("status") == "pending"]
        if not pending:
            return
        cfg = _merged_ba_config(None)
        # 守护配置的 pay_max_concurrent 叠加 (取较小值, 不超 _ba_config 限流)
        pay_cap = int(self.config.get("pay_max_concurrent", 3))
        cfg["max_concurrent"] = min(pay_cap, int(cfg.get("max_concurrent") or 3))
        started = 0
        for rec in pending:
            if not _ba_acquire_slot(cfg["max_concurrent"]):
                break  # 空位满, 停止本轮
            ba_token = rec.get("ba_token", "")
            ok, err = ba_try_start(ba_token)
            if not ok:
                _ba_release_slot()  # 占位失败, 释放刚拿的槽
                continue
            # 授权 config 合并: 守护层不覆盖每条 BA 记录的国家 (follow_chain_country 已生效)
            asyncio.create_task(_run_authorize_task(ba_token, cfg))
            self.stats["pay_started"] += 1
            started += 1
        if started:
            print(f"[pipeline] 支付段触发: started={started} pending={len(pending)}")

    # ---- 目标达成检查 ----
    def _check_target(self) -> None:
        """unlimited=False 时, pay_success >= target 则自动关开关。"""
        if bool(self.config.get("unlimited", True)):
            return
        target = int(self.config.get("target_accounts", 100))
        if target <= 0:
            return
        # 读取最新 pay_success (从 ba_queue 统计)
        try:
            from core.ba_queue import list_records
            pay_success = sum(1 for r in list_records() if r.get("status") == "success")
            self.stats["pay_success"] = pay_success
        except Exception:
            pay_success = self.stats.get("pay_success", 0)
        if pay_success >= target:
            print(f"[pipeline] 目标达成: pay_success={pay_success} >= target={target}, 自动停止")
            self.stop()

    # ---- 统计刷新 (从各段读最新值, 供 status 展示) ----
    def refresh_stats(self) -> None:
        """从 token_store / ba_queue 刷新累计统计 (而非依赖自增, 防漂移)。"""
        try:
            from core.token_store import token_store
            from core.ba_queue import list_records
            import asyncio as _aio
            loop = _aio.get_event_loop()
            if loop.is_running():
                # 在运行的事件循环里, 用 ensure_future 异步刷新 (不阻塞)
                asyncio.ensure_future(self._async_refresh())
            else:
                loop.run_until_complete(self._async_refresh())
        except Exception:
            pass

    async def _async_refresh(self) -> None:
        try:
            from core.token_store import token_store
            from core.ba_queue import list_records
            # reg_success = source=register 且 status != idle 的账号 (已进入提链/提链完)
            all_tokens = await token_store.list_tokens(source="register")
            self.stats["reg_success"] = sum(1 for t in all_tokens if t.get("status") != "idle")
            self.stats["chain_success"] = sum(1 for t in all_tokens if t.get("status") == "success")
            recs = list_records()
            self.stats["pay_success"] = sum(1 for r in recs if r.get("status") == "success")
        except Exception:
            pass


# 单例
pipeline_daemon = PipelineDaemon()
