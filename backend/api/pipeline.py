# -*- coding: utf-8 -*-
"""api/pipeline.py — 一键流程 REST 控制 (FastAPI Router)

  GET   /api/pipeline/status    守护状态 + 配置 + 统计 + 三段运行态
  POST  /api/pipeline/start      开启守护 (加载配置 → daemon.start)
  POST  /api/pipeline/stop       关闭守护 (cooperative drain, 不杀已运行任务)
  GET   /api/pipeline/config     读 pipeline_config.json
  POST  /api/pipeline/config     写 pipeline_config.json (原子落盘, 立即生效)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from core.pipeline_daemon import pipeline_daemon

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("/status")
async def pipeline_status() -> dict[str, Any]:
    """守护状态快照 (含三段运行态 + 统计)。"""
    pipeline_daemon.refresh_stats()
    return {"ok": True, **pipeline_daemon.status()}


@router.post("/start")
async def pipeline_start() -> dict[str, Any]:
    """开启守护循环。"""
    pipeline_daemon.start()
    return {"ok": True, "enabled": True, **pipeline_daemon.status()}


@router.post("/stop")
async def pipeline_stop() -> dict[str, Any]:
    """关闭守护 (cooperative: 已运行的注册/提链/支付任务自然完成, 不再触发新的)。"""
    pipeline_daemon.stop()
    return {"ok": True, "enabled": False, **pipeline_daemon.status()}


@router.get("/config")
async def get_pipeline_config() -> dict[str, Any]:
    return {"ok": True, "config": pipeline_daemon.config}


@router.post("/config")
async def update_pipeline_config(body: dict | None = None) -> dict[str, Any]:
    """更新守护配置 (原子落盘, 立即生效, 无需重启)。"""
    body = body or {}
    updates = {k: v for k, v in body.items() if v is not None}
    cfg = pipeline_daemon.update_config(updates)
    return {"ok": True, "config": cfg}
