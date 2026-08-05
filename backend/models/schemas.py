"""Pydantic 数据模型：API 请求/响应 schema。

定义 Token、链路、代理、统计等数据结构，供 REST 端点与 WebSocket 事件复用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Token 模型
# =============================================================================
class TokenImport(BaseModel):
    """批量导入请求体。"""
    raw: str = Field(..., description="多行 accessToken 或整段 Session JSON")


class TokenOut(BaseModel):
    """Token 列表/详情输出。"""
    id: str
    email: str = ""
    sub: str = ""
    account_id: str = ""
    plan_type: str = ""
    register_method: str = "email"  # email | phone
    expires_at: str = ""
    status: str = "idle"  # idle | running | success | failed | cooldown | expired
    created_at: str = ""
    last_run_at: str = ""

    class Config:
        from_attributes = True


class TokenListResponse(BaseModel):
    ok: bool = True
    tokens: list[TokenOut] = []
    total: int = 0


class TokenImportResponse(BaseModel):
    ok: bool = True
    imported: int = 0
    failed: int = 0
    tokens: list[TokenOut] = []
    error: str = ""


# =============================================================================
# 链路控制模型
# =============================================================================
class ChainBatchRequest(BaseModel):
    """批量启动链路。"""
    token_ids: list[str] = Field(default_factory=list)
    max_concurrent: int = 10
    retry_per_stage: int = 3
    attempts: int = 8
    auto_billing: bool = True
    require_zero: bool = True


class ChainStopRequest(BaseModel):
    """停止链路。"""
    chain_ids: Optional[list[str]] = None  # None=全部
    force: bool = False


class ChainStatus(BaseModel):
    running: bool
    active: int
    queued: int
    success: int
    failure: int
    chains: list[dict[str, Any]] = []


# =============================================================================
# 代理模型
# =============================================================================
class ProxyNode(BaseModel):
    """代理节点（sing-box / 隧道）。"""
    name: str
    type: str = "vless"  # vless | hysteria2 | tunnel
    country_hint: str = ""
    port: int = 0
    latency: int = 0          # ms, 0=未知
    healthy: Optional[bool] = None
    concurrent: int = 0
    max_concurrent: int = 3
    running: bool = False


class SubParseRequest(BaseModel):
    raw: str


class SubFetchRequest(BaseModel):
    url: str


class NodeToggleRequest(BaseModel):
    name: str


# =============================================================================
# 统计模型
# =============================================================================
class Stats(BaseModel):
    """累计统计。"""
    success: int = 0
    failure: int = 0
    byCountry: dict[str, int] = Field(default_factory=dict)
    failByCountry: dict[str, int] = Field(default_factory=dict)
    reasons: dict[str, int] = Field(default_factory=dict)
    stageMatrix: dict[str, dict[str, dict[str, int]]] = Field(default_factory=dict)


class StatsResponse(BaseModel):
    ok: bool = True
    stats: Stats


# =============================================================================
# 样本模型
# =============================================================================
class SampleRecord(BaseModel):
    ts: str
    email: str = ""
    ba: str = ""
    paypal_approve_url: str = ""
    pm_authorize_url: str = ""
    amount_due: int = 0
    currency: str = ""
    billing_country: str = ""
    payment_channel: str = ""
    reason_code: str = ""
    success: bool = True


# =============================================================================
# MoMo / 账单 / 配方模型
# =============================================================================
class MomoRequest(BaseModel):
    token_id: str
    patches: Optional[dict[str, bool]] = None


class BillingTemplate(BaseModel):
    country: str
    city: str
    state: str
    postal_code: str
    line1: str
    name: str


class FormulaRecord(BaseModel):
    """成功配方：一组曾经成功的国家组合。"""
    name: str
    checkout: str
    init: str
    provider: str
    approve: str
    poll: str
    resolve: str
    success_count: int = 0


# =============================================================================
# 通用响应
# =============================================================================
class OkResponse(BaseModel):
    ok: bool = True
    error: str = ""
    data: Any = None


# =============================================================================
# WebSocket 事件 (供参考, 实际推送为 dict)
# =============================================================================
class WSEvent(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
