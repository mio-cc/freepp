# -*- coding: utf-8 -*-
"""
provider_stats.py — 按邮箱 provider/domain 统计注册成功率，
并支持停用低成功率域名（某些临时邮箱域名会在最终 create_account 阶段
被 OpenAI 风控拒绝，停用后整体成功率显著提升）。

分桶（reason）：
    create_ok           注册成功
    otp_timeout         OTP 等待超时/无码
    upstream_502        上游读信失败（邮箱池 502 / UPSTREAM_READ_FAILED）
    already_registered  邮箱已在 OpenAI 注册（validate 返回 external_url）
    other_fail          其它失败（默认）

用法：
    import provider_stats as ps
    ps.record("someone@duck.com", success=True)                    # create_ok
    ps.record("x@outlook.com", success=False, reason="otp_timeout")
    if ps.is_blocked(email): ...
    print(ps.summary())
"""
import json
import threading
from pathlib import Path
from typing import Dict, Optional

STATS_FILE = Path(__file__).parent / "provider_stats.json"
BLOCK_THRESHOLD = 0.34     # 成功率低于此值的域名标记为停用
MIN_SAMPLES = 5            # 至少 N 次样本才参与评估（避免小样本误杀）

# 已知分桶；未知 reason 归入 other_fail
KNOWN_BUCKETS = (
    "create_ok",
    "otp_timeout",
    "upstream_502",
    "already_registered",
    "other_fail",
)

_lock = threading.Lock()
_data: Optional[Dict] = None


def _load() -> Dict:
    global _data
    if _data is not None:
        return _data
    if STATS_FILE.exists():
        try:
            _data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            _data = {}
    else:
        _data = {}
    _data.setdefault("domains", {})
    return _data


def _save():
    try:
        STATS_FILE.write_text(
            json.dumps(_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _domain_of(email: str) -> str:
    if not email or "@" not in email:
        return "(unknown)"
    return email.split("@")[-1].lower()


def _normalize_reason(success: bool, reason: Optional[str]) -> str:
    if success:
        return "create_ok"
    r = (reason or "other_fail").strip().lower() or "other_fail"
    if r in ("ok", "success", "create_ok"):
        return "other_fail"  # 失败路径不应标成功桶
    if r in KNOWN_BUCKETS:
        return r
    # 兼容常见别名
    aliases = {
        "timeout": "otp_timeout",
        "otp_empty": "otp_timeout",
        "no_otp": "otp_timeout",
        "502": "upstream_502",
        "upstream_read_failed": "upstream_502",
        "already_reg": "already_registered",
        "external_url": "already_registered",
        "registered": "already_registered",
    }
    return aliases.get(r, "other_fail")


def record(email: str, success: bool, reason: Optional[str] = None) -> str:
    """记录一次注册结果，返回域名。

    success=True 记 create_ok；success=False 时按 reason 分桶。
    兼容旧调用：record(email, success) 仍有效。
    """
    d = _load()
    dom = _domain_of(email)
    rec = d["domains"].setdefault(dom, {"ok": 0, "fail": 0, "buckets": {}})
    buckets = rec.setdefault("buckets", {})
    bucket = _normalize_reason(success, reason)
    buckets[bucket] = int(buckets.get(bucket) or 0) + 1
    if success:
        rec["ok"] += 1
    else:
        rec["fail"] += 1
    _save()
    return dom


def is_blocked(email: str) -> bool:
    """该域名是否因低成功率被停用（样本不足时不判停）。

    分桶注意：already_registered / otp_timeout 仍计入 fail，
    避免把「已注册号池」误判成「渠道本身可用」。若要按渠道健康度评估，
    请用 channel_fail_rate() 排除 already_registered。
    """
    d = _load()
    dom = _domain_of(email)
    rec = d["domains"].get(dom)
    if not rec:
        return False
    total = rec["ok"] + rec["fail"]
    if total < MIN_SAMPLES:
        return False
    rate = rec["ok"] / total
    return rate < BLOCK_THRESHOLD


def rate_of(email: str) -> Optional[float]:
    d = _load()
    rec = d["domains"].get(_domain_of(email))
    if not rec:
        return None
    total = rec["ok"] + rec["fail"]
    return (rec["ok"] / total) if total else None


def channel_fail_rate(email: str) -> Optional[float]:
    """渠道真实失败率：排除 already_registered（库存问题，非渠道故障）。"""
    d = _load()
    rec = d["domains"].get(_domain_of(email))
    if not rec:
        return None
    buckets = rec.get("buckets") or {}
    already = int(buckets.get("already_registered") or 0)
    ok = int(rec.get("ok") or 0)
    fail = int(rec.get("fail") or 0)
    # 有效样本 = 总样本 - 已注册（已注册既非 ok 也非渠道 fail）
    effective_total = ok + fail - already
    if effective_total <= 0:
        return None
    channel_fail = fail - already
    return channel_fail / effective_total


def summary() -> str:
    d = _load()
    lines = []
    for dom, rec in sorted(
        d["domains"].items(), key=lambda kv: -(kv[1]["ok"] + kv[1]["fail"])
    ):
        total = rec["ok"] + rec["fail"]
        rate = (rec["ok"] / total) if total else 0.0
        blocked = total >= MIN_SAMPLES and rate < BLOCK_THRESHOLD
        flag = " [已停用]" if blocked else ""
        buckets = rec.get("buckets") or {}
        bucket_parts = []
        for k in KNOWN_BUCKETS:
            n = int(buckets.get(k) or 0)
            if n:
                bucket_parts.append(f"{k}={n}")
        # 兼容历史：无 buckets 时只显示 ok/fail
        extra = (" | " + " ".join(bucket_parts)) if bucket_parts else ""
        lines.append(
            f"  {dom:26} ok={rec['ok']:3} fail={rec['fail']:3} "
            f"rate={rate*100:5.1f}%{flag}{extra}"
        )
    return "\n".join(lines) if lines else "  (暂无统计)"


if __name__ == "__main__":
    # 演示：低成功率域名会被标记；分桶可区分已注册 vs 真失败
    for _ in range(6):
        record("x@bad-temp.example", success=False, reason="other_fail")
    for _ in range(3):
        record("y@outlook.com", success=False, reason="already_registered")
    for _ in range(2):
        record("y@outlook.com", success=False, reason="otp_timeout")
    for _ in range(1):
        record("y@outlook.com", success=False, reason="upstream_502")
    for _ in range(6):
        record("z@good.example", success=True)
    print(summary())
    print("bad-temp blocked?", is_blocked("z@bad-temp.example"))
    print("good blocked?", is_blocked("z@good.example"))
    print("outlook channel_fail_rate", channel_fail_rate("a@outlook.com"))
