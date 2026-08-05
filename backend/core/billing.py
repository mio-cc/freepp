"""国家账单模板 + 青果隧道代理连接串构造。

数据源自原 config.py / geo_data / name_data / qg_area_codes（已内联，去除外部依赖）。
为全部 ALL_COUNTRIES 生成"贴近出口国"的 payment_method billing_details 模板。

连接串格式（指定国家，每次请求换 IP）：
    http://{authKey}:{authPwd}:A{areaCode}@overseas.tunnel.qg.net:{port}
"""
from __future__ import annotations

from typing import Any

from .config import settings

# --- 青果隧道海外区域编码 (area_code) 精选表 ---
# 完整 218 国来自 https://www.qg.net/doc/1975.html；此处内联主流 40 国。
AREA_CODES: dict[str, int] = {
    "US": 8, "GB": 2, "JP": 9, "HK": 11, "SG": 14, "KR": 16, "TW": 17,
    "AU": 13, "CA": 6, "DE": 21, "FR": 23, "NL": 27, "IE": 33, "BE": 34,
    "BR": 41, "MX": 43, "AR": 44, "CL": 45, "TH": 51, "VN": 52, "ID": 53,
    "MY": 54, "PH": 55, "IN": 56, "NZ": 61, "IT": 66, "ES": 67, "PT": 68,
    "SE": 71, "NO": 72, "DK": 73, "FI": 74, "CH": 76, "AT": 77, "PL": 81,
    "TR": 91, "AE": 101, "SA": 102, "IL": 103, "ZA": 111, "EG": 112,
}

# --- PayPal 不支持 / 风控高危国家黑名单 (OFAC + PayPal) ---
PAYPAL_BLOCKED: frozenset[str] = frozenset({
    "IR", "SY", "KP", "CU", "SD", "SS", "SO", "YE", "LY", "AF", "MM", "BY", "RU",
})

# --- 完整 ISO 3166 国家表 (216 国, 与前端国家下拉同源) ---
# 用于: 账单模板全量覆盖 + 前端国家选择 (auto + 200+ 国家)
ISO_COUNTRIES: list[str] = (
    "AD,AE,AF,AG,AI,AL,AM,AO,AR,AS,AT,AU,AW,AZ,BA,BB,BD,BE,BF,BG,BH,BI,BJ,BL,BM,BN,"
    "BO,BR,BS,BT,BW,BY,BZ,CA,CD,CF,CG,CH,CI,CK,CL,CM,CO,CR,CU,CV,CW,CY,CZ,DE,DJ,DK,"
    "DM,DO,DZ,EC,EE,EG,ER,ES,ET,EU,FI,FJ,FO,FR,GA,GB,GD,GE,GF,GH,GI,GL,GM,GN,GP,GQ,"
    "GR,GT,GU,GW,GY,HK,HN,HR,HT,HU,ID,IE,IL,IN,IQ,IR,IS,IT,JM,JO,JP,KE,KG,KH,KI,KM,"
    "KN,KR,KW,KY,KZ,LA,LB,LC,LI,LK,LR,LS,LT,LU,LV,LY,MA,MC,MD,ME,MG,MK,ML,MM,MN,MO,"
    "MQ,MR,MS,MT,MU,MV,MW,MX,MY,MZ,NA,NC,NE,NG,NI,NL,NO,NP,NZ,OM,PA,PE,PF,PG,PH,PK,"
    "PL,PM,PR,PT,PW,PY,QA,RE,RO,RS,RU,RW,SA,SB,SC,SD,SE,SG,SI,SK,SL,SM,SN,SO,SR,ST,"
    "SV,SX,SY,SZ,TC,TD,TG,TH,TJ,TL,TM,TN,TO,TR,TT,TW,TZ,UA,UG,US,UY,UZ,VC,VE,VG,VI,"
    "VN,VU,WS,YE,YT,ZA,ZM,ZW"
).split(",")

# 全部候选国家: ISO 全表 + AREA_CODES 并集, 排除黑名单
ALL_COUNTRIES: list[str] = sorted(
    set(ISO_COUNTRIES) | set(AREA_CODES) - set(PAYPAL_BLOCKED)
)

# --- 地理数据: (首都, 邮编样本, 币种) ---
GEO: dict[str, tuple[str, str, str]] = {
    "US": ("New York", "10001", "USD"),
    "GB": ("London", "SW1A 1AA", "GBP"),
    "JP": ("Tokyo", "100-0001", "JPY"),
    "HK": ("Hong Kong", "999077", "HKD"),
    "SG": ("Singapore", "048583", "SGD"),
    "KR": ("Seoul", "04524", "KRW"),
    "TW": ("Taipei", "100", "TWD"),
    "AU": ("Canberra", "2600", "AUD"),
    "CA": ("Ottawa", "K1A 0B1", "CAD"),
    "DE": ("Berlin", "10115", "EUR"),
    "FR": ("Paris", "75001", "EUR"),
    "NL": ("Amsterdam", "1011 AC", "EUR"),
    "IE": ("Dublin", "D01 F5P2", "EUR"),
    "BE": ("Brussels", "1000", "EUR"),
    "BR": ("Brasilia", "70040", "BRL"),
    "MX": ("Mexico City", "01000", "MXN"),
    "AR": ("Buenos Aires", "C1000", "ARS"),
    "CL": ("Santiago", "8320000", "CLP"),
    "TH": ("Bangkok", "10110", "THB"),
    "VN": ("Hanoi", "100000", "VND"),
    "ID": ("Jakarta", "10110", "IDR"),
    "MY": ("Kuala Lumpur", "50000", "MYR"),
    "PH": ("Manila", "1000", "PHP"),
    "IN": ("New Delhi", "110001", "INR"),
    "NZ": ("Wellington", "6011", "NZD"),
    "IT": ("Rome", "00100", "EUR"),
    "ES": ("Madrid", "28001", "EUR"),
    "PT": ("Lisbon", "1000-001", "EUR"),
    "SE": ("Stockholm", "111 21", "SEK"),
    "NO": ("Oslo", "0150", "NOK"),
    "DK": ("Copenhagen", "1050", "DKK"),
    "FI": ("Helsinki", "00100", "EUR"),
    "CH": ("Bern", "3000", "CHF"),
    "AT": ("Vienna", "1010", "EUR"),
    "PL": ("Warsaw", "00-001", "PLN"),
    "TR": ("Ankara", "06010", "TRY"),
    "AE": ("Abu Dhabi", "00000", "AED"),
    "SA": ("Riyadh", "11564", "SAR"),
    "IL": ("Jerusalem", "9100000", "ILS"),
    "ZA": ("Pretoria", "0001", "ZAR"),
    "EG": ("Cairo", "11511", "EGP"),
}

# --- 本地化人名 ---
NAMES: dict[str, str] = {
    "US": "John Smith", "GB": "James Wilson", "JP": "Haruto Sato",
    "HK": "Tai Man Chan", "SG": "Wei Ming Tan", "KR": "Min Jun Kim",
    "TW": "Wei Chen", "AU": "Jack Thompson", "CA": "William Brown",
    "DE": "Lukas Mueller", "FR": "Lucas Martin", "NL": "Daan de Vries",
    "IE": "Sean Murphy", "BE": "Luc Dubois", "BR": "Lucas Silva",
    "MX": "Diego Garcia", "AR": "Mateo Gonzalez", "CL": "Benjamin Lopez",
    "TH": "Somchai Jaidee", "VN": "Minh Nguyen", "ID": "Budi Santoso",
    "MY": "Ahmad Rahman", "PH": "Juan Santos", "IN": "Arjun Kumar",
    "NZ": "Oliver Wilson", "IT": "Alessandro Rossi", "ES": "Hugo Garcia",
    "PT": "Joao Silva", "SE": "Erik Andersson", "NO": "Lars Hansen",
    "DK": "Magnus Nielsen", "FI": "Matti Korhonen", "CH": "Luis Keller",
    "AT": "Paul Hofer", "PL": "Jan Kowalski", "TR": "Yusuf Yilmaz",
    "AE": "Ahmed Al Mansoori", "SA": "Abdullah Al Saud", "IL": "David Cohen",
    "ZA": "Thabo Mokoena", "EG": "Omar Hassan",
}

# 主流 12 国真实地标街道地址；其余用 "1 {首都} Main Street" 占位
_OVERRIDE_LINE1: dict[str, str] = {
    "US": "350 5th Ave", "AU": "1 Macquarie St", "GB": "10 Downing Street",
    "NL": "Damrak 1", "DE": "Invalidenstrasse 1", "FR": "10 Rue de Rivoli",
    "IE": "1 O'Connell Street", "BE": "Rue Neuve 1", "JP": "1-1 Chiyoda",
    "BR": "Av Paulista 1000", "HK": "1 Queens Road Central", "MO": "Av da Amizade 1",
}


def _build_billing_templates() -> dict[str, dict[str, str]]:
    templates: dict[str, dict[str, str]] = {}
    for ctry in ALL_COUNTRIES:
        geo = GEO.get(ctry)
        if geo is None:
            # 缺 GEO 的国家: 用国家码兜底 (账单模板仍需可提交)
            capital, postal, _currency = ctry, "00000", "USD"
        else:
            capital, postal, _currency = geo
        name = NAMES.get(ctry, "John Doe")
        line1 = _OVERRIDE_LINE1.get(ctry, f"1 {capital} Main Street")
        templates[ctry] = {
            "country": ctry,
            "city": capital,
            "state": capital,
            "postal_code": postal,
            "line1": line1,
            "name": name,
        }
    if "US" not in templates:
        templates["US"] = {
            "country": "US", "city": "New York", "state": "NY",
            "postal_code": "10001", "line1": "350 5th Ave", "name": "John Doe",
        }
    return templates


BILLING_TEMPLATES: dict[str, dict[str, str]] = _build_billing_templates()

# checkout 业务国家/币种矩阵
CHECKOUT_MATRIX: list[tuple[str, str]] = [
    ("US", "USD"),
    ("AU", "AUD"),
]

# 账单国 -> 币种 (GEO 第 3 列)
_BILLING_CURRENCY: dict[str, str] = {}
for _c, (_cap, _pc, _cur) in GEO.items():
    if _cur:
        _BILLING_CURRENCY[_c] = _cur


def billing_currency(country: str) -> str:
    """账单国对应的币种 (DE->EUR, US->USD...)，未知回退 USD。"""
    c = (country or "").upper()
    if c in _BILLING_CURRENCY:
        return _BILLING_CURRENCY[c]
    for cc, cur in CHECKOUT_MATRIX:
        if cc == c:
            return cur
    return "USD"


def billing_for(country: str, auto: bool = True, fallback: str = "US") -> dict[str, str]:
    """根据出口国家返回账单地址。

    auto=True:  账单贴近出口国。
    auto=False: 按 fallback 国。
    任何国家缺失则回退 US。
    """
    key = (country or fallback).upper()
    return dict(BILLING_TEMPLATES.get(key) or BILLING_TEMPLATES[fallback])


def tunnel_proxy(country: str, pool: str | None = None) -> str:
    """构造青果隧道代理连接串（指定国家，每次请求换 IP）。"""
    if pool is None:
        pool = settings.default_pool_name
    p = settings.qg_pool(pool)
    if not p:
        p = settings.qg_pool("qg_resi_pool")
    area = AREA_CODES.get((country or "").upper())
    if area is None:
        raise ValueError(f"未知国家 area_code: {country}")
    return f"http://{p['auth_key']}:{p['auth_pwd']}:A{area}@{p['host']}:{p['port']}"


def list_billing_templates() -> list[dict[str, str]]:
    """返回全部账单模板列表（供 /api/billing/templates）。"""
    return [dict(v, country=k) for k, v in sorted(BILLING_TEMPLATES.items())]
