# -*- coding: utf-8 -*-
"""美国免税地址库 (tax-free address store)。

五大免税州: DE(首选) / NH / MT / OR / AK
地址格式: street / city / state / zip / phone (usaddressgen 风格, 城市-州-邮编匹配)

支持:
  - 内置模板 (DE/NH/MT/OR/AK 真实城市 + 邮编 + 街道)
  - 随机/指定州取址
  - usaddressgen.com/tax-free-address/ 预留拉取 (dataUrls: us-data.json / us-cities.json)
"""
from __future__ import annotations

import json
import os
import random
import urllib.request
from typing import Any

# 五大免税州 + 真实城市/邮编组合 (city/state/zip 必须匹配)
TAX_FREE_STATES: dict[str, list[tuple[str, str]]] = {
    "DE": [  # 特拉华: 首选
        ("Wilmington", "19801"), ("Dover", "19901"), ("Newark", "19711"),
        ("Middletown", "19709"), ("Bear", "19701"),
    ],
    "NH": [  # 新罕布什尔
        ("Manchester", "03101"), ("Nashua", "03060"), ("Concord", "03301"),
        ("Portsmouth", "03801"), ("Derry", "03038"),
    ],
    "MT": [  # 蒙大拿
        ("Billings", "59101"), ("Missoula", "59801"), ("Great Falls", "59401"),
        ("Bozeman", "59715"), ("Helena", "59601"),
    ],
    "OR": [  # 俄勒冈
        ("Portland", "97205"), ("Salem", "97301"), ("Eugene", "97401"),
        ("Bend", "97701"), ("Medford", "97501"),
    ],
    "AK": [  # 阿拉斯加
        ("Anchorage", "99501"), ("Fairbanks", "99701"), ("Juneau", "99801"),
        ("Wasilla", "99654"), ("Sitka", "99835"),
    ],
}

# 街道模板 (usaddressgen 风格扰动)
_STREET_TEMPLATES = [
    "{} {} {}",  # number street suffix
]

_STREET_NAMES = [
    "Example Lane", "Sample Street", "Demo Road", "Main Street", "Oak Avenue",
    "Maple Drive", "Cedar Court", "Willow Way", "Pine Street", "Elm Boulevard",
    "Sunset Terrace", "Harbor View", "Market Street", "Highland Avenue",
]

_TAX_FREE_ZIP_RE = re_compiled = None  # noqa: F841  (占位)


def _phone() -> str:
    return f"({random.randint(200, 989)}) {random.randint(200, 989):03d}-{random.randint(1000, 9999):04d}"


def generate_address(state: str = "DE", name: str = "") -> dict[str, str]:
    """生成一个免税州地址 (street/city/state/zip/phone/name 匹配)。"""
    state = str(state or "DE").strip().upper()
    if state not in TAX_FREE_STATES:
        state = "DE"
    city, zip_code = random.choice(TAX_FREE_STATES[state])
    street = f"{random.randint(100, 4999)} {random.choice(_STREET_NAMES)}"
    return {
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "phone": _phone(),
        "name": name or "Simon Test",
        "full": f"{street}, {city}, {state} {zip_code}",
    }


def pick_state(prefer: str = "DE") -> str:
    """取州 (默认 DE 首选, 可显式指定)。"""
    s = str(prefer or "").strip().upper()
    if s in TAX_FREE_STATES:
        return s
    # 加权: DE 首选
    weights = {"DE": 60, "NH": 10, "MT": 10, "OR": 10, "AK": 10}
    pool = []
    for st, w in weights.items():
        pool += [st] * w
    return random.choice(pool)


def list_states() -> list[dict[str, str]]:
    """返回免税州列表 (含推荐标签)。"""
    notes = {
        "DE": "首选 · 无州/地方销售税",
        "NH": "推荐 · 数字商品免税",
        "MT": "推荐 · 数字商品免税",
        "OR": "推荐 · 数字商品免税",
        "AK": "部分地方税 7.5%",
    }
    return [{"state": s, "note": notes.get(s, "")} for s in TAX_FREE_STATES]


def fetch_usaddressgen(state: str = "DE") -> dict[str, Any] | None:
    """预留: 从 usaddressgen.com 拉取真实免税地址。

    站点数据: /data/us-data.<hash>.json (含城市/邮编), 此处预留实现,
    网络不可用时回退本地模板。
    """
    try:
        urls = [
            "https://usaddressgen.com/data/us-data.51b467380d1255919aab05ff0d5836ab44b800a9906a1e2bd2d48815aed30996.json",
        ]
        req = urllib.request.Request(urls[0], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


taxfree_store = {
    "generate": generate_address,
    "pick_state": pick_state,
    "list_states": list_states,
    "fetch_usaddressgen": fetch_usaddressgen,
}
