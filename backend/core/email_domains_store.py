"""email_domains_store.py — 邮箱域名池用户可配置存储 (去硬编码)。

把 PayPal 提链流程里按国家硬编码的邮箱域名池 (country_profile._EMAIL_DOMAINS /
identity_lib._EMAIL_DOMAINS / models._BR_EMAIL_DOMAINS) 集中到 email_domains.json,
前端「密钥与凭据」页可读可写; 无配置文件时回落到内置默认, 保持向后兼容。

存储位置: backend/email_domains.json (与 ba_config.json 同级, 不进 git)。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_FILE = Path(__file__).resolve().parent.parent / "email_domains.json"

# ── 内置默认 (原硬编码值, 作为无配置文件时的回退) ──────────────────────
_DEFAULT_BY_COUNTRY: dict[str, list[str]] = {
    "US": ["gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "icloud.com", "protonmail.com"],
    "GB": ["gmail.com", "outlook.com", "hotmail.co.uk", "yahoo.co.uk", "icloud.com", "btinternet.com"],
    "AU": ["gmail.com", "outlook.com", "yahoo.com.au", "hotmail.com", "icloud.com", "bigpond.com"],
    "DE": ["gmx.de", "web.de", "gmail.com", "outlook.de", "yahoo.de", "t-online.de"],
    "JP": ["gmail.com", "yahoo.co.jp", "icloud.com", "outlook.jp", "docomo.ne.jp", "ezweb.ne.jp"],
    "TH": ["gmail.com", "hotmail.com", "yahoo.com", "outlook.com", "icloud.com", "mail.com"],
    "NL": ["gmail.com", "outlook.com", "hotmail.com", "ziggo.nl", "kpnmail.nl", "icloud.com"],
    "VN": ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "mail.com", "zoho.com"],
    "BH": ["gmail.com", "hotmail.com", "outlook.com", "batelco.com.bh", "yahoo.com", "icloud.com"],
    "AO": ["gmail.com", "hotmail.com", "yahoo.com", "outlook.com", "netcabo.co.ao", "mail.com"],
    "AE": ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "etisalat.ae", "icloud.com"],
    "CI": ["gmail.com", "yahoo.fr", "hotmail.com", "outlook.com", "icloud.com", "afribone.net"],
    "TR": ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yandex.com", "icloud.com"],
    "KR": ["gmail.com", "naver.com", "hanmail.net", "nate.com", "outlook.com", "kakao.com"],
    "BR": ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com.br", "icloud.com", "bol.com.br"],
    "MX": ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com.mx", "prodigy.net.mx", "icloud.com"],
    "TW": ["gmail.com", "yahoo.com.tw", "hotmail.com", "outlook.com", "icloud.com", "pchome.com.tw"],
}

_DEFAULT_FALLBACK: list[str] = [
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com",
    "icloud.com", "protonmail.com", "mail.com",
]


class EmailDomainsStore:
    """email_domains.json 单例: 按国家域名池 + 全局 fallback。"""

    def __init__(self) -> None:
        self._by_country: dict[str, list[str]] = {}
        self._fallback: list[str] = []
        self.load()

    def load(self) -> None:
        try:
            if not _CONFIG_FILE.exists():
                return
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                bc = raw.get("by_country")
                if isinstance(bc, dict):
                    self._by_country = {
                        str(k).upper(): [str(d) for d in v if isinstance(d, str)]
                        for k, v in bc.items() if isinstance(v, list)
                    }
                fb = raw.get("fallback")
                if isinstance(fb, list):
                    self._fallback = [str(d) for d in fb if isinstance(d, str)]
        except Exception:
            pass

    def _save(self) -> None:
        try:
            tmp = str(_CONFIG_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"by_country": self._by_country, "fallback": self._fallback},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, _CONFIG_FILE)
        except OSError:
            pass

    def get_all(self) -> dict[str, Any]:
        """返回完整配置 (前端编辑用), 含内置默认 (合并后)。"""
        return {
            "by_country": {k: list(v) for k, v in self._by_country.items()},
            "fallback": list(self._fallback),
            "defaults": {
                "by_country": {k: list(v) for k, v in _DEFAULT_BY_COUNTRY.items()},
                "fallback": list(_DEFAULT_FALLBACK),
            },
        }

    def update(self, by_country: dict[str, list[str]] | None = None,
               fallback: list[str] | None = None) -> dict[str, Any]:
        if by_country is not None:
            self._by_country = {
                str(k).upper(): [str(d) for d in v if isinstance(d, str)]
                for k, v in by_country.items() if isinstance(v, list)
            }
        if fallback is not None:
            self._fallback = [str(d) for d in fallback if isinstance(d, str)]
        self._save()
        return self.get_all()

    def reset(self) -> dict[str, Any]:
        self._by_country = {}
        self._fallback = []
        self._save()
        return self.get_all()

    # ---- 读取接口 (运行时调用) ----
    def domains_for_country(self, country: str) -> list[str]:
        """按国家取域名池: 用户配置 > 内置默认。"""
        c = (country or "").upper()
        if c in self._by_country and self._by_country[c]:
            return list(self._by_country[c])
        if c in _DEFAULT_BY_COUNTRY:
            return list(_DEFAULT_BY_COUNTRY[c])
        return self.fallback_domains()

    def fallback_domains(self) -> list[str]:
        """全局 fallback 池: 用户配置 > 内置默认。"""
        if self._fallback:
            return list(self._fallback)
        return list(_DEFAULT_FALLBACK)


# 全局单例
email_domains_store = EmailDomainsStore()
