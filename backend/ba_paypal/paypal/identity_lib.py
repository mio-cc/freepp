"""PayPal 各国注册表单字段生成库 (按国家分类, 校验位算法对齐官方/公开实现)。

依据:
- 前端 bundle main_*.js 的 kycFields 配置 (gj/_js/main_72d757f6c90e0b683d47_js.js)
- 各国身份号公开校验算法 (泰国 mod-11 / 阿联酋 Luhn / 韩国 RRN 加权 mod-11 /
  南非 Luhn / 阿根廷 CUIT mod-11 / 墨西哥 CURP base37 mod-10 / 越南 CCCD 结构 /
  巴林 CPR 格式, 校验未公开仅格式 / 巴西 CPF mod-11 / 德国 IBAN mod-97)
- len(字段值) 与表单 maxlength 一致, dateOfBirth 使用 18+ 合法日期
"""

from __future__ import annotations

import calendar
from datetime import date
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

# =============================================================================
# 通用校验位工具
# =============================================================================


def _luhn_check_digit(partial: str) -> int:
    """Luhn (mod-10) 校验位: 从右往左, 隔位乘 2, >9 减 9, 总和补 0/10。"""
    total = 0
    alternate = True
    for ch in reversed(partial):
        d = int(ch)
        if alternate:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alternate = not alternate
    return 0 if total % 10 == 0 else 10 - (total % 10)


def _verify_luhn(number: str) -> bool:
    total = 0
    alternate = False
    for ch in reversed(number):
        d = int(ch)
        if alternate:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alternate = not alternate
    return total % 10 == 0


def _mod11_check_digit(base: str, weights) -> tuple[int, int]:
    """模-11 校验: sum(d_i*w_i) mod 11; 返回 (remainder, candidate)。"""
    total = sum(int(d) * w for d, w in zip(base, weights))
    return total % 11, total


def _mod11_check_digit_v2(base: str, weights) -> int:
    """泰国 PIN: check = (11 - sum%11) % 10。"""
    rem, _ = _mod11_check_digit(base, weights)
    return (11 - rem) % 10


def _mod11_check_digit_kr(base: str) -> int:
    """韩国 RRN: (11 - sum%11) mod 10。"""
    rem, _ = _mod11_check_digit(base, [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5])
    return (11 - rem) % 10


def _mod11_check_digit_ar(base: str) -> Optional[int]:
    """阿根廷 CUIT: weights 5,4,3,2,7,6,5,4,3,2; 11->0, 10->非法(None)。"""
    rem, _ = _mod11_check_digit(base, [5, 4, 3, 2, 7, 6, 5, 4, 3, 2])
    if rem == 0:
        return 0
    check = 11 - rem
    if check == 11:
        return 0
    if check == 10:
        return None
    return check


def _mod97_iban_check_digits(country: str, bban: str) -> str:
    """ISO 13616 IBAN 校验位 (mod-97): DE + check + bban。"""
    rearranged = bban + country + "00"
    number = int("".join(str(int(c, 36)) if c.isalpha() else c for c in rearranged))
    return f"{98 - (number % 97):02d}"


# =============================================================================
# 通用姓名/日期/邮箱
# =============================================================================

_EMAIL_DOMAINS = [
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com",
    "icloud.com", "protonmail.com", "mail.com",
]


def generate_dob(min_year: int = 1965, max_year: int = 2002, fmt: str = "%d/%m/%Y") -> str:
    """生成成年合法生日 (默认 DD/MM/YYYY, PayPal 表单通常用 dd/MM/y)。"""
    year = random.randint(min_year, max_year)
    month = random.randint(1, 12)
    day = random.randint(1, calendar.monthrange(year, month)[1])
    return date(year, month, day).strftime(fmt)


def _country_email_domain(country: str) -> Optional[str]:
    """按国家取邮箱域名池 (国家信号), 未收录国家回退通用池。"""
    try:
        from paypal.country_profile import email_domains
        return random.choice(email_domains(country))
    except Exception:
        return None


def generate_email(first: str, last: str, domain: Optional[str] = None, country: str = "") -> str:
    fb = _fallback_domains()
    d = domain or _country_email_domain(country) or (random.choice(fb) if fb else None)
    if not d:
        d = "gmail.com"
    return f"{first.lower().replace(' ', '')}.{last.lower().replace(' ', '')}{random.randint(10, 99999)}@{d}"


def _fallback_domains() -> list[str]:
    """全局 fallback 域名池: 优先用户配置, 回退内置默认。"""
    try:
        from core.email_domains_store import email_domains_store
        return email_domains_store.fallback_domains()
    except Exception:
        return list(_EMAIL_DOMAINS)
    return f"{first.lower().replace(' ', '')}.{last.lower().replace(' ', '')}{random.randint(10, 99999)}@{d}"


def generate_password(min_len: int = 10, max_len: int = 16) -> str:
    lower = "abcdefghijklmnopqrstuvwxyz"
    upper = lower.upper()
    required = "0123456789!@#$%&*"
    chars = lower + upper + required
    length = random.randint(min_len, max_len)
    pwd = [random.choice(required)]
    while len(pwd) < length:
        pwd.append(random.choice(chars))
    random.shuffle(pwd)
    return "".join(pwd)


# =============================================================================
# 各国电话/地址生成 (国家信号对齐: 号码长度/前缀/地址语义)
# =============================================================================

# 国家 -> (长度, 首位限定) 全国本国不完全精确但结构真实 (只用于 signup 联系方式展示,
# 实际 2FA 接收号码来自接码平台, phone 仅与 phoneCountry 对齐)。
_PHONE_NATIONAL: dict[str, tuple[int, list[str]]] = {
    "US": (10, ["2", "3", "4", "5", "6", "7", "8", "9"]),
    "GB": (10, ["7"]),            # 07XXXXXXXX 移动
    "AU": (9, ["4"]),             # 04XXXXXXXX 移动
    "DE": (10, ["1", "2", "3", "4", "5", "6", "7", "8"]),
    "JP": (10, ["7", "8", "9"]),
    "TH": (9, ["6", "8", "9"]),   # 08/09/06XXXXXXXX
    "NL": (9, ["6"]),
    "VN": (9, ["3", "5", "7", "8", "9"]),
    "BH": (8, ["3", "6"]),
    "AO": (9, ["9"]),
    "AE": (9, ["5"]),             # 05XXXXXXXX
    "CI": (10, ["5", "7", "1"]),
    "TR": (10, ["5"]),            # 05XXXXXXXX
    "BR": (10, ["6", "7", "8", "9", "1"]),   # 无区号 9xxxx-xxxx (11 位含区号)
    "KR": (9, ["1"]),             # 01X-XXX-XXXX
    "MX": (10, ["5", "6", "7", "8", "9"]),  # 55/56XXXXXXXX 移动 (10 位无区号)
    "TW": (9, ["9"]),              # 09XXXXXXXX 移动
}


def _generate_national_phone(country: str) -> str:
    spec = _PHONE_NATIONAL.get(country.upper(), (10, ["1", "2", "3", "4", "5", "6", "7", "8", "9"]))
    length, firsts = spec
    return random.choice(firsts) + "".join(str(random.randint(0, 9)) for _ in range(length - 1))


def _generate_postal(spec: dict, prefix: str) -> str:
    """按邮政编码格式规则 (postal_spec) 算法生成有效邮编。

    依据各国邮编官方格式 (不查表, 由规则推导), prefix 为该区域邮编外码区段,
    保证生成的邮编落在该街道所在城市/州区。已联网核对过的格式:
      - digits: 固定长度纯数字 (US 5 / AU 4 / TH 5 / VN 5 / TR 5 / KR 5 / MX 5 / TW 3)
      - gb_post: 英国 AN NAA / AAN NAA (E1 6AN) — 外码 prefix + 内码随机字母数字
      - nl_post: 荷兰 NNNN LL (1011 AB) — 4 位数字 prefix + 2 位字母(避开 C/S/M/O/I/J/Q/U/V)
      - jp_post: 日本 NNN-NNNN (100-0001) — 3 位 prefix + 4 位随机
      - br_cep: 巴西 CEP NNNNN-NNN (01310-100) — 5 位 prefix + 3 位随机
      - ci_bp: 科特迪瓦 "NN BP N" 邮政信箱 (01 BP 1) — prefix 2 位区号
      - fixed: 无统一邮编国家 (AO/BH/AE) 直接用 region 给定的真实信箱号
    """
    kind = (spec or {}).get("kind", "digits")
    if kind == "fixed":
        # 无统一邮编: 用区域真实信箱/区码 (postal_prefix 即完整邮编)。
        return prefix
    if kind == "digits":
        length = int(spec.get("length", 5))
        allow_all_zero = bool(spec.get("allow_all_zero", False))
        # 阿联酋等无标准邮编国家: 官方建议填 00000, 直接返回全 0 长度位。
        if allow_all_zero:
            return "0" * length
        suffix_len = max(0, length - len(prefix))
        for _ in range(50):  # 重试避免全 0 / 非法前导
            suffix = "".join(str(random.randint(0, 9)) for _ in range(suffix_len))
            code = prefix + suffix
            if not code.startswith("0" * length):  # 非全 0
                return code
        return code  # 兜底
    if kind == "gb_post":
        # 英国邮编: <outward><space><inward>; outward=prefix(如 E1/AAN), inward=数字+2字母。
        # 内码首位 1-9, 末两位字母 (避开 C/I,K,M,O,V 易混字母)。
        inner_num = random.randint(1, 9)
        # 官方 inward 字母集排除 C I K M O V (与数字/字义混); 用稳定子集。
        letters = "ABDEFGHJLNPQRSTUWXYZ"
        a1, a2 = random.choice(letters), random.choice(letters)
        return f"{prefix} {inner_num}{a1}{a2}"
    if kind == "nl_post":
        # 荷兰: 4 位数字 + 空格 + 2 字母; 字母集排除 C/S/M/O (易与数字/字义混), 不含首字母 U/W/Z。
        # 官方规则: 第二个字母不能为 SA/SS/SD/SJ/SZ 等; 用宽子集 ABDEFGHJLNPQRST。
        letters = "ABDEFGHJLNPQRST"
        a1, a2 = random.choice(letters), random.choice(letters)
        return f"{prefix} {a1}{a2}"
    if kind == "jp_post":
        # 日本: NNN-NNNN, 4 位后缀首位可 0。
        suffix = "".join(str(random.randint(0, 9)) for _ in range(4))
        return f"{prefix}-{suffix}"
    if kind == "br_cep":
        # 巴西 CEP: NNNNN-NNN。prefix 给 3 位前 (如 013), 补 2 位到 5 位, 再 3 位随机。
        suffix5 = "".join(str(random.randint(0, 9)) for _ in range(max(0, 5 - len(prefix))))
        suffix3 = "".join(str(random.randint(0, 9)) for _ in range(3))
        return f"{prefix}{suffix5}-{suffix3}"  # 形如 01310-100
    if kind == "ci_bp":
        # 科特迪瓦: "NN BP N" (区号 BP 信箱号)。
        box = random.randint(1, 9999)
        return f"{prefix} BP {box}"
    # 未知 kind 回退纯数字。
    length = int(spec.get("length", 5))
    suffix_len = max(0, length - len(prefix))
    return prefix + "".join(str(random.randint(0, 9)) for _ in range(suffix_len))


def generate_country_address(country: str) -> dict:
    """按国家生成账单地址 (含 line2 语义: district/apartment/empty)。

    地址一致性由算法保证: 先随机选一个 region (city/state 区段), 从该 region 取街道,
    再用该 region 的 postal_prefix + 国家 postal_spec 规则算法生成邮编。这样:
      - 邮编格式永远合法 (各国邮编规则);
      - 街道与邮编同属一个城市/州区 (区域锚定), 不再出现 street/postal 跨区错配。
    PayPal 端再经 AddressAutocompleteFromPostalCodeQuery 做最终校验/补全。
    """
    cc = (country or "").upper()
    try:
        from paypal.country_profile import address_pool
        pool = address_pool(cc)
    except Exception:
        # 兜底: 纽约 5 位 ZIP 区段。
        pool = dict(postal_spec=dict(kind="digits", length=5), regions=[
            dict(city="New York", state="NY", postal_prefix="100", line2_policy="apartment",
                 streets=("350 5th Ave", "215 W 34th St", "10 E 33rd St", "55 W 25th St"))])

    spec = pool.get("postal_spec") or dict(kind="digits", length=5)
    regions = pool.get("regions") or []
    if not regions:
        # 旧式扁平结构兼容 (仅兜底用)。
        regions = [dict(city=pool.get("city", "New York"), state=pool.get("state", "NY"),
                        postal_prefix=pool.get("postal_prefix", "100"),
                        streets=pool.get("streets", ("350 5th Ave",)),
                        line2_policy=pool.get("line2_policy", "apartment"))]
    region = random.choice(regions)
    line1 = random.choice(region["streets"])
    postal_code = _generate_postal(spec, region.get("postal_prefix", ""))
    policy = region.get("line2_policy", "apartment")
    if policy == "district":
        line2 = f"Centro {random.randint(1, 900)}"
    elif policy == "apartment":
        line2 = f"Apt {random.randint(100, 900)}"
    else:
        line2 = ""
    return {
        "line1": line1,
        "line2": line2,
        "city": region["city"],
        "state": region["state"],
        "postal_code": postal_code,
        "country": cc,
    }


def generate_country_phone(country: str) -> tuple[str, str]:
    """返回 (phone_country 前缀, 完整号码)。"""
    cc = (country or "").upper()
    try:
        from paypal.country_profile import _COUNTRY_MAP
        prefix = _COUNTRY_MAP[cc]["phone"]
    except Exception:
        prefix = "+1"
    return prefix, f"{prefix}{_generate_national_phone(cc)}"


# =============================================================================
# 各国卡 BIN 池 (bin, 长度, issuer, product_class, cvv_len)
# =============================================================================

CARD_BINS: dict[str, list[tuple[str, int, str, str, int]]] = {
    # BR 池扩充 (2026-08-14): 全部来自公开 BIN/IIN 目录 (bincheck/binx/freebinchecker),
    # 覆盖 Banco do Brasil / Itau / Bradesco / Caixa / Santander / Nubank / Inter / Mercado Pago / Wise 等
    "BR": [
        ("414709", 16, "VISA", "CREDIT", 3), ("516292", 16, "MASTER_CARD", "CREDIT", 3),
        ("455187", 16, "VISA", "DEBIT", 3), ("504427", 16, "MASTER_CARD", "DEBIT", 3),
        # Banco do Brasil VISA
        ("400130", 16, "VISA", "CREDIT", 3), ("400162", 16, "VISA", "CREDIT", 3),
        ("400168", 16, "VISA", "CREDIT", 3), ("400174", 16, "VISA", "CREDIT", 3),
        ("400178", 16, "VISA", "CREDIT", 3), ("400184", 16, "VISA", "CREDIT", 3),
        ("400187", 16, "VISA", "CREDIT", 3), ("400191", 16, "VISA", "CREDIT", 3),
        ("400196", 16, "VISA", "CREDIT", 3), ("403792", 16, "VISA", "CREDIT", 3),
        ("403797", 16, "VISA", "CREDIT", 3), ("423072", 16, "VISA", "CREDIT", 3),
        ("448460", 16, "VISA", "CREDIT", 3), ("498401", 16, "VISA", "CREDIT", 3),
        ("498406", 16, "VISA", "CREDIT", 3), ("498407", 16, "VISA", "CREDIT", 3),
        ("498408", 16, "VISA", "CREDIT", 3), ("498442", 16, "VISA", "CREDIT", 3),
        ("498453", 16, "VISA", "CREDIT", 3), ("400102", 16, "VISA", "DEBIT", 3),
        # Itau / Itaucard VISA
        ("400234", 16, "VISA", "CREDIT", 3), ("400235", 16, "VISA", "CREDIT", 3),
        ("400247", 16, "VISA", "CREDIT", 3), ("400253", 16, "VISA", "CREDIT", 3),
        ("400268", 16, "VISA", "CREDIT", 3), ("400635", 16, "VISA", "CREDIT", 3),
        ("403798", 16, "VISA", "CREDIT", 3), ("411049", 16, "VISA", "CREDIT", 3),
        ("417874", 16, "VISA", "CREDIT", 3), ("452407", 16, "VISA", "CREDIT", 3),
        ("459078", 16, "VISA", "CREDIT", 3), ("470598", 16, "VISA", "CREDIT", 3),
        ("489423", 16, "VISA", "CREDIT", 3), ("490172", 16, "VISA", "CREDIT", 3),
        # Bradesco VISA
        ("400453", 16, "VISA", "CREDIT", 3), ("400455", 16, "VISA", "CREDIT", 3),
        ("406655", 16, "VISA", "CREDIT", 3), ("406669", 16, "VISA", "CREDIT", 3),
        ("409603", 16, "VISA", "CREDIT", 3), ("429768", 16, "VISA", "CREDIT", 3),
        ("440693", 16, "VISA", "CREDIT", 3), ("455183", 16, "VISA", "CREDIT", 3),
        # Caixa VISA
        ("400236", 16, "VISA", "CREDIT", 3), ("400957", 16, "VISA", "CREDIT", 3),
        ("421960", 16, "VISA", "CREDIT", 3), ("421961", 16, "VISA", "CREDIT", 3),
        ("426055", 16, "VISA", "CREDIT", 3), ("459383", 16, "VISA", "CREDIT", 3),
        ("474539", 16, "VISA", "CREDIT", 3), ("479395", 16, "VISA", "CREDIT", 3),
        # Santander VISA
        ("401638", 16, "VISA", "CREDIT", 3), ("410863", 16, "VISA", "CREDIT", 3),
        ("422061", 16, "VISA", "CREDIT", 3), ("425850", 16, "VISA", "CREDIT", 3),
        ("441524", 16, "VISA", "CREDIT", 3),
        # 其他 BR 发卡行 VISA
        ("401132", 16, "VISA", "CREDIT", 3), ("401165", 16, "VISA", "CREDIT", 3),
        ("402762", 16, "VISA", "CREDIT", 3), ("407843", 16, "VISA", "CREDIT", 3),
        ("409007", 16, "VISA", "CREDIT", 3), ("415274", 16, "VISA", "CREDIT", 3),
        ("446690", 16, "VISA", "CREDIT", 3),
        # BR Mastercard
        ("222985", 16, "MASTER_CARD", "CREDIT", 3), ("230744", 16, "MASTER_CARD", "CREDIT", 3),
        ("234087", 16, "MASTER_CARD", "CREDIT", 3), ("514945", 16, "MASTER_CARD", "CREDIT", 3),
        ("515590", 16, "MASTER_CARD", "CREDIT", 3), ("521397", 16, "MASTER_CARD", "CREDIT", 3),
        ("525663", 16, "MASTER_CARD", "CREDIT", 3), ("539614", 16, "MASTER_CARD", "CREDIT", 3),
        ("541555", 16, "MASTER_CARD", "CREDIT", 3), ("542820", 16, "MASTER_CARD", "CREDIT", 3),
        ("544731", 16, "MASTER_CARD", "CREDIT", 3), ("550209", 16, "MASTER_CARD", "CREDIT", 3),
        ("552236", 16, "MASTER_CARD", "CREDIT", 3), ("552305", 16, "MASTER_CARD", "CREDIT", 3),
        ("553636", 16, "MASTER_CARD", "CREDIT", 3), ("553647", 16, "MASTER_CARD", "CREDIT", 3),
        ("554281", 16, "MASTER_CARD", "CREDIT", 3), ("554775", 16, "MASTER_CARD", "CREDIT", 3),
        ("554953", 16, "MASTER_CARD", "CREDIT", 3), ("556024", 16, "MASTER_CARD", "CREDIT", 3),
        ("558297", 16, "MASTER_CARD", "CREDIT", 3), ("558383", 16, "MASTER_CARD", "CREDIT", 3),
        ("558645", 16, "MASTER_CARD", "CREDIT", 3), ("556670", 16, "MASTER_CARD", "CREDIT", 3),
        ("554417", 16, "MASTER_CARD", "CREDIT", 3), ("549021", 16, "MASTER_CARD", "CREDIT", 3),
    ],
    # US 池扩充 (2026-08-14): 公开 BIN/IIN 目录 (binlist.io/bincheck.org/creditcardvalidator/bindb),
    # 覆盖 Chase / Bank of America / Wells Fargo / Citibank / Capital One / U.S. Bank / Discover / Amex
    "US": [
        # Chase VISA
        ("414720", 16, "VISA", "CREDIT", 3), ("475050", 16, "VISA", "CREDIT", 3),
        ("401135", 16, "VISA", "CREDIT", 3), ("401136", 16, "VISA", "CREDIT", 3),
        ("402297", 16, "VISA", "CREDIT", 3), ("438857", 16, "VISA", "CREDIT", 3),
        ("436610", 16, "VISA", "CREDIT", 3), ("436611", 16, "VISA", "CREDIT", 3),
        ("436617", 16, "VISA", "CREDIT", 3),
        # Bank of America VISA
        ("414716", 16, "VISA", "CREDIT", 3), ("449533", 16, "VISA", "CREDIT", 3),
        ("401901", 16, "VISA", "CREDIT", 3), ("401902", 16, "VISA", "CREDIT", 3),
        ("402076", 16, "VISA", "CREDIT", 3), ("435680", 16, "VISA", "DEBIT", 3),
        ("435681", 16, "VISA", "DEBIT", 3), ("435682", 16, "VISA", "DEBIT", 3),
        # Wells Fargo VISA
        ("416724", 16, "VISA", "DEBIT", 3), ("434256", 16, "VISA", "DEBIT", 3),
        ("434257", 16, "VISA", "DEBIT", 3), ("473099", 16, "VISA", "DEBIT", 3),
        ("475637", 16, "VISA", "DEBIT", 3), ("400151", 16, "VISA", "DEBIT", 3),
        ("400173", 16, "VISA", "DEBIT", 3), ("400205", 16, "VISA", "DEBIT", 3),
        # Citibank
        ("414711", 16, "VISA", "CREDIT", 3), ("400919", 16, "VISA", "CREDIT", 3),
        ("400927", 16, "VISA", "CREDIT", 3), ("230050", 16, "MASTER_CARD", "DEBIT", 3),
        # Capital One
        ("400344", 16, "VISA", "CREDIT", 3), ("401472", 16, "VISA", "CREDIT", 3),
        ("402265", 16, "VISA", "CREDIT", 3), ("486236", 16, "VISA", "CREDIT", 3),
        ("517805", 16, "MASTER_CARD", "CREDIT", 3),
        # U.S. Bank
        ("408022", 16, "VISA", "DEBIT", 3), ("408845", 16, "VISA", "CREDIT", 3),
        ("408846", 16, "VISA", "CREDIT", 3), ("408847", 16, "VISA", "CREDIT", 3),
        ("436618", 16, "VISA", "DEBIT", 3), ("414780", 16, "VISA", "CREDIT", 3),
        # 其他 US 发卡行
        ("440319", 16, "VISA", "CREDIT", 3), ("415874", 16, "VISA", "DEBIT", 3),
        ("482870", 16, "VISA", "DEBIT", 3),
        ("553370", 16, "MASTER_CARD", "CREDIT", 3), ("548009", 16, "MASTER_CARD", "CREDIT", 3),
        ("475423", 16, "VISA", "DEBIT", 3), ("475427", 16, "VISA", "DEBIT", 3),
        ("517669", 16, "MASTER_CARD", "CREDIT", 3),
        ("517869", 16, "MASTER_CARD", "DEBIT", 3),
        ("601100", 16, "DISCOVER", "CREDIT", 3), ("601101", 16, "DISCOVER", "CREDIT", 3),
        ("373197", 15, "AMEX", "CREDIT", 4), ("373198", 15, "AMEX", "CREDIT", 4),
        ("373432", 15, "AMEX", "CREDIT", 4),
    ],
    # JP 池扩充: Rakuten / Mitsubishi UFJ Nicos / SMBC / Saison / Mizuho / EPOS / JCB
    "JP": [
        # Rakuten
        ("429769", 16, "VISA", "CREDIT", 3), ("429770", 16, "VISA", "CREDIT", 3),
        ("429771", 16, "VISA", "CREDIT", 3), ("429772", 16, "VISA", "CREDIT", 3),
        ("465993", 16, "VISA", "CREDIT", 3), ("466778", 16, "VISA", "CREDIT", 3),
        ("492371", 16, "VISA", "CREDIT", 3), ("492372", 16, "VISA", "CREDIT", 3),
        # Mitsubishi UFJ Nicos / MUFG
        ("453450", 16, "VISA", "CREDIT", 3), ("521231", 16, "MASTER_CARD", "CREDIT", 3),
        ("521232", 16, "MASTER_CARD", "CREDIT", 3), ("521233", 16, "MASTER_CARD", "CREDIT", 3),
        ("521234", 16, "MASTER_CARD", "CREDIT", 3), ("521253", 16, "MASTER_CARD", "CREDIT", 3),
        ("521255", 16, "MASTER_CARD", "CREDIT", 3), ("521257", 16, "MASTER_CARD", "CREDIT", 3),
        ("222924", 16, "MASTER_CARD", "CREDIT", 3),
        # Sumitomo Mitsui (SMBC)
        ("498001", 16, "VISA", "CREDIT", 3), ("530232", 16, "MASTER_CARD", "CREDIT", 3),
        ("533491", 16, "MASTER_CARD", "CREDIT", 3), ("222880", 16, "MASTER_CARD", "CREDIT", 3),
        ("222897", 16, "MASTER_CARD", "CREDIT", 3),
        # Saison / Mizuho / EPOS
        ("454153", 16, "VISA", "CREDIT", 3), ("454294", 16, "VISA", "CREDIT", 3),
        ("489784", 16, "VISA", "CREDIT", 3), ("377783", 15, "AMEX", "CREDIT", 4),
        # JCB (泛化段 35xx 本身即 JCB 网络, 类型恒对应)
        ("35", 16, "JCB", "CREDIT", 3),
    ],
    # GB 池扩充: Barclays / Lloyds / HSBC / NatWest / Santander / RBS / MBNA / Tesco Bank
    "GB": [
        # Barclays VISA
        ("402147", 16, "VISA", "CREDIT", 3), ("402148", 16, "VISA", "CREDIT", 3),
        ("402152", 16, "VISA", "CREDIT", 3), ("409023", 16, "VISA", "CREDIT", 3),
        ("409024", 16, "VISA", "CREDIT", 3), ("409025", 16, "VISA", "CREDIT", 3),
        ("409026", 16, "VISA", "CREDIT", 3), ("412280", 16, "VISA", "CREDIT", 3),
        ("412282", 16, "VISA", "CREDIT", 3), ("412991", 16, "VISA", "CREDIT", 3),
        ("412992", 16, "VISA", "CREDIT", 3), ("412993", 16, "VISA", "CREDIT", 3),
        ("425757", 16, "VISA", "CREDIT", 3), ("426501", 16, "VISA", "CREDIT", 3),
        ("426525", 16, "VISA", "CREDIT", 3), ("427700", 16, "VISA", "CREDIT", 3),
        ("429595", 16, "VISA", "CREDIT", 3), ("447318", 16, "VISA", "CREDIT", 3),
        ("449355", 16, "VISA", "CREDIT", 3), ("451154", 16, "VISA", "CREDIT", 3),
        ("451155", 16, "VISA", "CREDIT", 3), ("461250", 16, "VISA", "CREDIT", 3),
        ("462747", 16, "VISA", "CREDIT", 3), ("485859", 16, "VISA", "CREDIT", 3),
        ("400115", 16, "VISA", "DEBIT", 3), ("408367", 16, "VISA", "DEBIT", 3),
        ("409400", 16, "VISA", "DEBIT", 3), ("409401", 16, "VISA", "DEBIT", 3),
        ("409402", 16, "VISA", "DEBIT", 3), ("430532", 16, "VISA", "DEBIT", 3),
        ("453978", 16, "VISA", "DEBIT", 3), ("453979", 16, "VISA", "DEBIT", 3),
        ("456725", 16, "VISA", "DEBIT", 3), ("465858", 16, "VISA", "DEBIT", 3),
        ("465859", 16, "VISA", "DEBIT", 3), ("465861", 16, "VISA", "DEBIT", 3),
        ("492826", 16, "VISA", "DEBIT", 3), ("492827", 16, "VISA", "DEBIT", 3),
        # Barclays Mastercard
        ("513624", 16, "MASTER_CARD", "CREDIT", 3), ("514021", 16, "MASTER_CARD", "CREDIT", 3),
        ("539616", 16, "MASTER_CARD", "CREDIT", 3), ("540002", 16, "MASTER_CARD", "CREDIT", 3),
        ("542607", 16, "MASTER_CARD", "CREDIT", 3), ("543247", 16, "MASTER_CARD", "CREDIT", 3),
        # Lloyds Mastercard
        ("540055", 16, "MASTER_CARD", "CREDIT", 3), ("540403", 16, "MASTER_CARD", "CREDIT", 3),
        ("540427", 16, "MASTER_CARD", "CREDIT", 3), ("540429", 16, "MASTER_CARD", "CREDIT", 3),
        ("540431", 16, "MASTER_CARD", "CREDIT", 3), ("540436", 16, "MASTER_CARD", "CREDIT", 3),
        ("540437", 16, "MASTER_CARD", "CREDIT", 3), ("540456", 16, "MASTER_CARD", "CREDIT", 3),
        ("540463", 16, "MASTER_CARD", "CREDIT", 3), ("540471", 16, "MASTER_CARD", "CREDIT", 3),
        ("540485", 16, "MASTER_CARD", "CREDIT", 3), ("540493", 16, "MASTER_CARD", "CREDIT", 3),
        ("542309", 16, "MASTER_CARD", "CREDIT", 3), ("542502", 16, "MASTER_CARD", "CREDIT", 3),
        # HSBC
        ("486460", 16, "VISA", "CREDIT", 3), ("485738", 16, "VISA", "CREDIT", 3),
        ("447692", 16, "VISA", "CREDIT", 3), ("540251", 16, "MASTER_CARD", "CREDIT", 3),
        ("540252", 16, "MASTER_CARD", "CREDIT", 3), ("540903", 16, "MASTER_CARD", "CREDIT", 3),
        ("542101", 16, "MASTER_CARD", "CREDIT", 3), ("542597", 16, "MASTER_CARD", "CREDIT", 3),
        ("542854", 16, "MASTER_CARD", "CREDIT", 3), ("543131", 16, "MASTER_CARD", "CREDIT", 3),
        # NatWest / RBS (VISA debit, Mastercard credit)
        ("475110", 16, "VISA", "DEBIT", 3), ("475116", 16, "VISA", "DEBIT", 3),
        ("475117", 16, "VISA", "DEBIT", 3), ("475118", 16, "VISA", "DEBIT", 3),
        ("540964", 16, "MASTER_CARD", "CREDIT", 3), ("542451", 16, "MASTER_CARD", "CREDIT", 3),
        ("542515", 16, "MASTER_CARD", "CREDIT", 3), ("542516", 16, "MASTER_CARD", "CREDIT", 3),
        ("542533", 16, "MASTER_CARD", "CREDIT", 3), ("543166", 16, "MASTER_CARD", "CREDIT", 3),
        ("541170", 16, "MASTER_CARD", "CREDIT", 3), ("542004", 16, "MASTER_CARD", "CREDIT", 3),
        ("542615", 16, "MASTER_CARD", "CREDIT", 3),
        # Santander UK
        ("475714", 16, "VISA", "DEBIT", 3), ("528689", 16, "MASTER_CARD", "CREDIT", 3),
        ("541002", 16, "MASTER_CARD", "CREDIT", 3), ("541361", 16, "MASTER_CARD", "CREDIT", 3),
        ("541603", 16, "MASTER_CARD", "CREDIT", 3), ("541647", 16, "MASTER_CARD", "CREDIT", 3),
        # MBNA / Tesco Bank / Aqua
        ("540635", 16, "MASTER_CARD", "CREDIT", 3), ("540758", 16, "MASTER_CARD", "CREDIT", 3),
        ("512687", 16, "MASTER_CARD", "CREDIT", 3), ("557098", 16, "MASTER_CARD", "CREDIT", 3),
    ],
    # DE 池扩充: Deutsche Bank / DKB / N26
    "DE": [
        # Deutsche Bank VISA
        ("404546", 16, "VISA", "CREDIT", 3), ("404547", 16, "VISA", "CREDIT", 3),
        ("416090", 16, "VISA", "CREDIT", 3), ("416091", 16, "VISA", "CREDIT", 3),
        ("416092", 16, "VISA", "CREDIT", 3), ("416093", 16, "VISA", "CREDIT", 3),
        ("430514", 16, "VISA", "CREDIT", 3), ("441233", 16, "VISA", "CREDIT", 3),
        ("441258", 16, "VISA", "CREDIT", 3), ("441259", 16, "VISA", "CREDIT", 3),
        ("441260", 16, "VISA", "CREDIT", 3), ("441261", 16, "VISA", "CREDIT", 3),
        ("441262", 16, "VISA", "CREDIT", 3), ("441263", 16, "VISA", "CREDIT", 3),
        ("441264", 16, "VISA", "CREDIT", 3), ("441287", 16, "VISA", "CREDIT", 3),
        ("441288", 16, "VISA", "CREDIT", 3), ("441293", 16, "VISA", "CREDIT", 3),
        ("441298", 16, "VISA", "CREDIT", 3), ("448401", 16, "VISA", "CREDIT", 3),
        ("451853", 16, "VISA", "CREDIT", 3), ("451854", 16, "VISA", "CREDIT", 3),
        ("460190", 16, "VISA", "CREDIT", 3), ("460191", 16, "VISA", "CREDIT", 3),
        ("474588", 16, "VISA", "CREDIT", 3), ("477912", 16, "VISA", "CREDIT", 3),
        ("477913", 16, "VISA", "CREDIT", 3), ("485700", 16, "VISA", "CREDIT", 3),
        ("485701", 16, "VISA", "CREDIT", 3), ("485702", 16, "VISA", "CREDIT", 3),
        ("486455", 16, "VISA", "CREDIT", 3), ("486456", 16, "VISA", "CREDIT", 3),
        # Deutsche Bank Mastercard
        ("512665", 16, "MASTER_CARD", "CREDIT", 3), ("519375", 16, "MASTER_CARD", "CREDIT", 3),
        ("523227", 16, "MASTER_CARD", "CREDIT", 3), ("523230", 16, "MASTER_CARD", "CREDIT", 3),
        ("523276", 16, "MASTER_CARD", "CREDIT", 3), ("545105", 16, "MASTER_CARD", "CREDIT", 3),
        ("545990", 16, "MASTER_CARD", "CREDIT", 3), ("545991", 16, "MASTER_CARD", "CREDIT", 3),
        ("547268", 16, "MASTER_CARD", "CREDIT", 3), ("547341", 16, "MASTER_CARD", "CREDIT", 3),
        ("557011", 16, "MASTER_CARD", "CREDIT", 3),
        # DKB (Lufthansa Miles & More 等)
        ("499897", 16, "VISA", "CREDIT", 3), ("523403", 16, "MASTER_CARD", "CREDIT", 3),
        ("523407", 16, "MASTER_CARD", "CREDIT", 3), ("523412", 16, "MASTER_CARD", "CREDIT", 3),
        ("523417", 16, "MASTER_CARD", "CREDIT", 3), ("523420", 16, "MASTER_CARD", "CREDIT", 3),
        ("523423", 16, "MASTER_CARD", "CREDIT", 3), ("523428", 16, "MASTER_CARD", "CREDIT", 3),
        ("523430", 16, "MASTER_CARD", "CREDIT", 3), ("523435", 16, "MASTER_CARD", "CREDIT", 3),
        ("523437", 16, "MASTER_CARD", "CREDIT", 3), ("523439", 16, "MASTER_CARD", "CREDIT", 3),
        ("523443", 16, "MASTER_CARD", "CREDIT", 3), ("523447", 16, "MASTER_CARD", "CREDIT", 3),
        ("523449", 16, "MASTER_CARD", "CREDIT", 3), ("523451", 16, "MASTER_CARD", "CREDIT", 3),
        ("523453", 16, "MASTER_CARD", "CREDIT", 3), ("523455", 16, "MASTER_CARD", "CREDIT", 3),
        ("523464", 16, "MASTER_CARD", "CREDIT", 3), ("523468", 16, "MASTER_CARD", "CREDIT", 3),
        ("523471", 16, "MASTER_CARD", "CREDIT", 3), ("523472", 16, "MASTER_CARD", "CREDIT", 3),
        ("523476", 16, "MASTER_CARD", "CREDIT", 3), ("523477", 16, "MASTER_CARD", "CREDIT", 3),
        ("523480", 16, "MASTER_CARD", "CREDIT", 3), ("523483", 16, "MASTER_CARD", "CREDIT", 3),
        ("523484", 16, "MASTER_CARD", "CREDIT", 3), ("523488", 16, "MASTER_CARD", "CREDIT", 3),
        ("523491", 16, "MASTER_CARD", "CREDIT", 3), ("523492", 16, "MASTER_CARD", "CREDIT", 3),
        ("523495", 16, "MASTER_CARD", "CREDIT", 3),
        # N26
        ("535584", 16, "MASTER_CARD", "DEBIT", 3), ("535585", 16, "MASTER_CARD", "DEBIT", 3),
        ("535586", 16, "MASTER_CARD", "DEBIT", 3), ("535590", 16, "MASTER_CARD", "DEBIT", 3),
    ],
    # TH 池扩充: Bangkok Bank / Kasikorn / Krungthai / SCB / Krungsri / UOB / TMB / Thanachart / Citi
    "TH": [
        # Bangkok Bank VISA
        ("404870", 16, "VISA", "CREDIT", 3), ("404871", 16, "VISA", "CREDIT", 3),
        ("404872", 16, "VISA", "CREDIT", 3), ("404873", 16, "VISA", "CREDIT", 3),
        ("404875", 16, "VISA", "CREDIT", 3), ("404876", 16, "VISA", "CREDIT", 3),
        ("448427", 16, "VISA", "CREDIT", 3), ("454624", 16, "VISA", "CREDIT", 3),
        ("454626", 16, "VISA", "CREDIT", 3), ("454627", 16, "VISA", "CREDIT", 3),
        ("454631", 16, "VISA", "CREDIT", 3), ("454632", 16, "VISA", "CREDIT", 3),
        ("473014", 16, "VISA", "CREDIT", 3), ("421315", 16, "VISA", "DEBIT", 3),
        ("454630", 16, "VISA", "DEBIT", 3), ("462288", 16, "VISA", "DEBIT", 3),
        # Bangkok Bank Mastercard
        ("544464", 16, "MASTER_CARD", "CREDIT", 3), ("544469", 16, "MASTER_CARD", "CREDIT", 3),
        ("544482", 16, "MASTER_CARD", "CREDIT", 3), ("544485", 16, "MASTER_CARD", "CREDIT", 3),
        ("544488", 16, "MASTER_CARD", "CREDIT", 3),
        # Kasikorn
        ("402339", 16, "VISA", "CREDIT", 3), ("406230", 16, "VISA", "CREDIT", 3),
        ("428380", 16, "VISA", "CREDIT", 3), ("431508", 16, "VISA", "CREDIT", 3),
        ("438278", 16, "VISA", "CREDIT", 3), ("492141", 16, "VISA", "CREDIT", 3),
        ("541176", 16, "MASTER_CARD", "CREDIT", 3), ("540488", 16, "MASTER_CARD", "CREDIT", 3),
        # Krungthai Card
        ("439111", 16, "VISA", "CREDIT", 3), ("439112", 16, "VISA", "CREDIT", 3),
        ("439113", 16, "VISA", "CREDIT", 3), ("439114", 16, "VISA", "CREDIT", 3),
        ("439121", 16, "VISA", "CREDIT", 3), ("439122", 16, "VISA", "CREDIT", 3),
        ("439127", 16, "VISA", "CREDIT", 3), ("540604", 16, "MASTER_CARD", "CREDIT", 3),
        ("540605", 16, "MASTER_CARD", "CREDIT", 3), ("540716", 16, "MASTER_CARD", "CREDIT", 3),
        # Siam Commercial Bank
        ("434087", 16, "VISA", "CREDIT", 3), ("434088", 16, "VISA", "CREDIT", 3),
        ("434089", 16, "VISA", "CREDIT", 3), ("454852", 16, "VISA", "CREDIT", 3),
        ("490733", 16, "VISA", "CREDIT", 3), ("534442", 16, "MASTER_CARD", "CREDIT", 3),
        ("540492", 16, "MASTER_CARD", "CREDIT", 3), ("541029", 16, "MASTER_CARD", "CREDIT", 3),
        ("541496", 16, "MASTER_CARD", "CREDIT", 3), ("541897", 16, "MASTER_CARD", "CREDIT", 3),
        # Bank of Ayudhya (Krungsri)
        ("424953", 16, "VISA", "CREDIT", 3), ("424954", 16, "VISA", "CREDIT", 3),
        ("450580", 16, "VISA", "CREDIT", 3), ("455205", 16, "VISA", "CREDIT", 3),
        ("455296", 16, "VISA", "CREDIT", 3), ("540430", 16, "MASTER_CARD", "CREDIT", 3),
        ("540474", 16, "MASTER_CARD", "CREDIT", 3), ("541690", 16, "MASTER_CARD", "CREDIT", 3),
        # Citi / TMB / Thanachart / UOB / SCB Thai / Krung Thai / GSB / Aeon
        ("438679", 16, "VISA", "CREDIT", 3), ("454325", 16, "VISA", "CREDIT", 3),
        ("455596", 16, "VISA", "CREDIT", 3), ("540432", 16, "MASTER_CARD", "CREDIT", 3),
        ("436759", 16, "VISA", "CREDIT", 3), ("442308", 16, "VISA", "CREDIT", 3),
        ("540040", 16, "MASTER_CARD", "CREDIT", 3), ("414167", 16, "VISA", "CREDIT", 3),
        ("540180", 16, "MASTER_CARD", "CREDIT", 3), ("541878", 16, "MASTER_CARD", "CREDIT", 3),
        ("407539", 16, "VISA", "CREDIT", 3), ("436807", 16, "VISA", "CREDIT", 3),
        ("437750", 16, "VISA", "CREDIT", 3), ("541859", 16, "MASTER_CARD", "CREDIT", 3),
        ("453215", 16, "VISA", "DEBIT", 3), ("449932", 16, "VISA", "CREDIT", 3),
        ("451485", 16, "VISA", "CREDIT", 3), ("409061", 16, "VISA", "CREDIT", 3),
        ("409062", 16, "VISA", "CREDIT", 3),
    ],
    "KR": [("4", 16, "VISA", "CREDIT", 3), ("53", 16, "MASTER_CARD", "DEBIT", 3), ("35", 16, "JCB", "CREDIT", 3)],
    "AU": [("4", 16, "VISA", "CREDIT", 3), ("52", 16, "MASTER_CARD", "DEBIT", 3)],
    # VN 池扩充: Vietcombank / Sacombank / VPBank / MB / BIDV / VIB / MSB / SCB / Shinhan / HDBank / SeABank / OCB / VietinBank / LPB / PVComBank / SHB (公开 IIN 目录 + SBV 官方)
    "VN": [
        # Vietcombank
        ("403277", 16, "VISA", "DEBIT", 3), ("428310", 16, "VISA", "DEBIT", 3),
        ("452404", 16, "VISA", "DEBIT", 3), ("477390", 16, "VISA", "DEBIT", 3),
        ("222806", 16, "MASTER_CARD", "CREDIT", 3), ("526418", 16, "MASTER_CARD", "DEBIT", 3),
        # Sacombank
        ("401520", 16, "VISA", "DEBIT", 3), ("422151", 16, "VISA", "DEBIT", 3),
        ("436438", 16, "VISA", "CREDIT", 3), ("455376", 16, "VISA", "CREDIT", 3),
        ("461138", 16, "VISA", "DEBIT", 3), ("461140", 16, "VISA", "CREDIT", 3),
        ("461337", 16, "VISA", "CREDIT", 3), ("466243", 16, "VISA", "CREDIT", 3),
        ("469654", 16, "VISA", "DEBIT", 3), ("472074", 16, "VISA", "CREDIT", 3),
        ("472075", 16, "VISA", "CREDIT", 3), ("486265", 16, "VISA", "CREDIT", 3),
        ("512341", 16, "MASTER_CARD", "CREDIT", 3), ("526830", 16, "MASTER_CARD", "CREDIT", 3),
        ("552332", 16, "MASTER_CARD", "CREDIT", 3), ("517416", 16, "MASTER_CARD", "DEBIT", 3),
        # VPBank
        ("405280", 16, "VISA", "CREDIT", 3), ("406453", 16, "VISA", "CREDIT", 3),
        ("419834", 16, "VISA", "CREDIT", 3), ("454107", 16, "VISA", "CREDIT", 3),
        ("478668", 16, "VISA", "CREDIT", 3), ("454119", 16, "VISA", "DEBIT", 3),
        ("518966", 16, "MASTER_CARD", "CREDIT", 3), ("520399", 16, "MASTER_CARD", "CREDIT", 3),
        ("523975", 16, "MASTER_CARD", "CREDIT", 3), ("524394", 16, "MASTER_CARD", "CREDIT", 3),
        ("520395", 16, "MASTER_CARD", "DEBIT", 3), ("521377", 16, "MASTER_CARD", "DEBIT", 3),
        ("528626", 16, "MASTER_CARD", "DEBIT", 3),
        # MB
        ("472674", 16, "VISA", "CREDIT", 3), ("484803", 16, "VISA", "CREDIT", 3),
        ("484804", 16, "VISA", "CREDIT", 3), ("548566", 16, "MASTER_CARD", "DEBIT", 3),
        # BIDV / VIB
        ("402534", 16, "VISA", "CREDIT", 3), ("436467", 16, "VISA", "CREDIT", 3),
        ("436468", 16, "VISA", "CREDIT", 3), ("457560", 16, "VISA", "DEBIT", 3),
        ("457561", 16, "VISA", "DEBIT", 3),
        ("498766", 16, "VISA", "CREDIT", 3), ("498767", 16, "VISA", "CREDIT", 3),
        ("498768", 16, "VISA", "DEBIT", 3), ("498769", 16, "VISA", "DEBIT", 3),
        # MSB / SCB / Shinhan / HDBank
        ("402204", 16, "VISA", "DEBIT", 3), ("402215", 16, "VISA", "DEBIT", 3),
        ("412189", 16, "VISA", "CREDIT", 3), ("472265", 16, "VISA", "CREDIT", 3),
        ("479155", 16, "VISA", "CREDIT", 3),
        ("453618", 16, "VISA", "DEBIT", 3), ("489516", 16, "VISA", "CREDIT", 3),
        ("489517", 16, "VISA", "CREDIT", 3), ("489518", 16, "VISA", "CREDIT", 3),
        ("510235", 16, "MASTER_CARD", "CREDIT", 3), ("545579", 16, "MASTER_CARD", "CREDIT", 3),
        ("554627", 16, "MASTER_CARD", "CREDIT", 3), ("550796", 16, "MASTER_CARD", "DEBIT", 3),
        ("430389", 16, "VISA", "CREDIT", 3), ("516294", 16, "MASTER_CARD", "CREDIT", 3),
        ("532451", 16, "MASTER_CARD", "CREDIT", 3), ("510995", 16, "MASTER_CARD", "DEBIT", 3),
        ("511409", 16, "MASTER_CARD", "DEBIT", 3), ("521976", 16, "MASTER_CARD", "DEBIT", 3),
        ("416259", 16, "VISA", "CREDIT", 3), ("462478", 16, "VISA", "CREDIT", 3),
        ("515131", 16, "MASTER_CARD", "CREDIT", 3), ("532137", 16, "MASTER_CARD", "DEBIT", 3),
        # SeABank / OCB / VietinBank / LPB / PVComBank / SHB
        ("405082", 16, "VISA", "DEBIT", 3), ("436545", 16, "VISA", "CREDIT", 3),
        ("436546", 16, "VISA", "CREDIT", 3), ("476636", 16, "VISA", "CREDIT", 3),
        ("523611", 16, "MASTER_CARD", "CREDIT", 3), ("540392", 16, "MASTER_CARD", "DEBIT", 3),
        ("442415", 16, "VISA", "DEBIT", 3), ("442416", 16, "VISA", "DEBIT", 3),
        ("421595", 16, "VISA", "DEBIT", 3), ("462842", 16, "VISA", "CREDIT", 3),
        ("462843", 16, "VISA", "CREDIT", 3), ("462844", 16, "VISA", "CREDIT", 3),
        ("469672", 16, "VISA", "CREDIT", 3), ("469673", 16, "VISA", "CREDIT", 3),
        ("413534", 16, "VISA", "CREDIT", 3), ("413535", 16, "VISA", "CREDIT", 3),
        ("406598", 16, "VISA", "CREDIT", 3), ("418248", 16, "VISA", "DEBIT", 3),
        ("511962", 16, "MASTER_CARD", "CREDIT", 3), ("538742", 16, "MASTER_CARD", "CREDIT", 3),
        ("542553", 16, "MASTER_CARD", "CREDIT", 3), ("519501", 16, "MASTER_CARD", "CREDIT", 3),
        ("528645", 16, "MASTER_CARD", "DEBIT", 3), ("533147", 16, "MASTER_CARD", "CREDIT", 3),
        ("533968", 16, "MASTER_CARD", "CREDIT", 3), ("559270", 16, "MASTER_CARD", "CREDIT", 3),
    ],
    "BH": [("4", 16, "VISA", "CREDIT", 3), ("53", 16, "MASTER_CARD", "DEBIT", 3)],
    "AE": [("4", 16, "VISA", "CREDIT", 3), ("51", 16, "MASTER_CARD", "DEBIT", 3)],
    "TR": [("4", 16, "VISA", "CREDIT", 3), ("51", 16, "MASTER_CARD", "DEBIT", 3), ("9792", 16, "TROY", "CREDIT", 3)],
    # NL 池扩充: ABN AMRO / Rabobank / ING / International Card Services / ANWB / Stripe / Amex
    "NL": [
        ("456353", 16, "VISA", "CREDIT", 3), ("456354", 16, "VISA", "CREDIT", 3),
        ("472906", 16, "VISA", "DEBIT", 3), ("405629", 16, "VISA", "CREDIT", 3),
        ("417274", 16, "VISA", "CREDIT", 3),
        ("400850", 16, "VISA", "CREDIT", 3), ("400851", 16, "VISA", "CREDIT", 3),
        ("400852", 16, "VISA", "CREDIT", 3), ("400853", 16, "VISA", "CREDIT", 3),
        ("400854", 16, "VISA", "CREDIT", 3), ("400855", 16, "VISA", "CREDIT", 3),
        ("400856", 16, "VISA", "CREDIT", 3), ("400857", 16, "VISA", "CREDIT", 3),
        ("400858", 16, "VISA", "CREDIT", 3), ("400859", 16, "VISA", "CREDIT", 3),
        ("522078", 16, "MASTER_CARD", "CREDIT", 3), ("534126", 16, "MASTER_CARD", "CREDIT", 3),
        ("520953", 16, "MASTER_CARD", "CREDIT", 3), ("520639", 16, "MASTER_CARD", "CREDIT", 3),
        ("524886", 16, "MASTER_CARD", "CREDIT", 3), ("532964", 16, "MASTER_CARD", "CREDIT", 3),
        ("532965", 16, "MASTER_CARD", "CREDIT", 3), ("553417", 16, "MASTER_CARD", "CREDIT", 3),
        ("555220", 16, "MASTER_CARD", "CREDIT", 3), ("555221", 16, "MASTER_CARD", "CREDIT", 3),
        ("555308", 16, "MASTER_CARD", "CREDIT", 3), ("555309", 16, "MASTER_CARD", "CREDIT", 3),
        ("555310", 16, "MASTER_CARD", "CREDIT", 3), ("555311", 16, "MASTER_CARD", "CREDIT", 3),
        ("556681", 16, "MASTER_CARD", "CREDIT", 3), ("523635", 16, "MASTER_CARD", "CREDIT", 3),
        ("523636", 16, "MASTER_CARD", "CREDIT", 3),
        ("510008", 16, "MASTER_CARD", "CREDIT", 3), ("541330", 16, "MASTER_CARD", "CREDIT", 3),
        ("375309", 15, "AMEX", "CREDIT", 4), ("375331", 15, "AMEX", "CREDIT", 4),
        ("375335", 15, "AMEX", "CREDIT", 4), ("375368", 15, "AMEX", "CREDIT", 4),
        ("375388", 15, "AMEX", "CREDIT", 4),
    ],
    "CI": [("4", 16, "VISA", "CREDIT", 3), ("51", 16, "MASTER_CARD", "DEBIT", 3)],
    "AO": [("4", 16, "VISA", "CREDIT", 3), ("51", 16, "MASTER_CARD", "DEBIT", 3)],
    # MX 池扩充 (2026-08-14): Banorte / BBVA Bancomer / Banamex / Santander / HSBC / Scotiabank / Azteca / Invex
    "MX": [
        # Banorte VISA
        ("418925", 16, "VISA", "CREDIT", 3), ("491341", 16, "VISA", "CREDIT", 3),
        ("491366", 16, "VISA", "CREDIT", 3), ("491375", 16, "VISA", "CREDIT", 3),
        ("491376", 16, "VISA", "CREDIT", 3), ("491575", 16, "VISA", "CREDIT", 3),
        ("491576", 16, "VISA", "CREDIT", 3), ("493158", 16, "VISA", "CREDIT", 3),
        ("493172", 16, "VISA", "CREDIT", 3), ("493173", 16, "VISA", "CREDIT", 3),
        ("491566", 16, "VISA", "DEBIT", 3), ("495166", 16, "VISA", "DEBIT", 3),
        # Banorte Mastercard
        ("544549", 16, "MASTER_CARD", "CREDIT", 3), ("547078", 16, "MASTER_CARD", "CREDIT", 3),
        ("547096", 16, "MASTER_CARD", "CREDIT", 3),
        # BBVA Bancomer
        ("408176", 16, "VISA", "DEBIT", 3), ("409851", 16, "VISA", "DEBIT", 3),
        ("410177", 16, "VISA", "DEBIT", 3), ("410180", 16, "VISA", "CREDIT", 3),
        ("410181", 16, "VISA", "CREDIT", 3), ("415231", 16, "VISA", "DEBIT", 3),
        ("415327", 16, "VISA", "CREDIT", 3), ("418073", 16, "VISA", "CREDIT", 3),
        ("418075", 16, "VISA", "CREDIT", 3), ("418077", 16, "VISA", "CREDIT", 3),
        ("418080", 16, "VISA", "CREDIT", 3), ("418093", 16, "VISA", "CREDIT", 3),
        ("418094", 16, "VISA", "CREDIT", 3), ("441310", 16, "VISA", "CREDIT", 3),
        ("441311", 16, "VISA", "CREDIT", 3), ("441314", 16, "VISA", "CREDIT", 3),
        ("441312", 16, "VISA", "DEBIT", 3), ("441313", 16, "VISA", "DEBIT", 3),
        ("444085", 16, "VISA", "CREDIT", 3), ("444086", 16, "VISA", "CREDIT", 3),
        ("446117", 16, "VISA", "DEBIT", 3), ("446118", 16, "VISA", "DEBIT", 3),
        ("455500", 16, "VISA", "CREDIT", 3), ("455503", 16, "VISA", "CREDIT", 3),
        ("455504", 16, "VISA", "CREDIT", 3), ("455505", 16, "VISA", "CREDIT", 3),
        ("493160", 16, "VISA", "CREDIT", 3), ("493161", 16, "VISA", "CREDIT", 3),
        ("493162", 16, "VISA", "CREDIT", 3), ("494398", 16, "VISA", "CREDIT", 3),
        ("498585", 16, "VISA", "CREDIT", 3),
        # Banamex / Santander / HSBC / Scotiabank / Azteca / Invex
        ("441541", 16, "VISA", "CREDIT", 3), ("441545", 16, "VISA", "DEBIT", 3),
        ("441549", 16, "VISA", "DEBIT", 3), ("451331", 16, "VISA", "DEBIT", 3),
        ("433465", 16, "VISA", "CREDIT", 3), ("441507", 16, "VISA", "CREDIT", 3),
        ("451299", 16, "VISA", "CREDIT", 3), ("451312", 16, "VISA", "DEBIT", 3),
        ("547046", 16, "MASTER_CARD", "CREDIT", 3),
        ("441551", 16, "VISA", "CREDIT", 3), ("452412", 16, "VISA", "DEBIT", 3),
        ("444449", 16, "VISA", "CREDIT", 3), ("441548", 16, "VISA", "CREDIT", 3),
        ("446137", 16, "VISA", "CREDIT", 3),
    ],
    # IN 池扩充 (2026-08-14): HDFC / ICICI / SBI / Axis
    "IN": [
        # HDFC VISA credit
        ("401403", 16, "VISA", "CREDIT", 3), ("402219", 16, "VISA", "CREDIT", 3),
        ("402359", 16, "VISA", "CREDIT", 3), ("404249", 16, "VISA", "CREDIT", 3),
        ("404276", 16, "VISA", "CREDIT", 3), ("405028", 16, "VISA", "CREDIT", 3),
        ("406578", 16, "VISA", "CREDIT", 3), ("407497", 16, "VISA", "CREDIT", 3),
        ("407498", 16, "VISA", "CREDIT", 3), ("416317", 16, "VISA", "CREDIT", 3),
        ("417410", 16, "VISA", "CREDIT", 3), ("418136", 16, "VISA", "CREDIT", 3),
        ("418218", 16, "VISA", "CREDIT", 3), ("424246", 16, "VISA", "CREDIT", 3),
        ("425698", 16, "VISA", "CREDIT", 3), ("430570", 16, "VISA", "CREDIT", 3),
        ("434155", 16, "VISA", "CREDIT", 3), ("434168", 16, "VISA", "CREDIT", 3),
        ("434677", 16, "VISA", "CREDIT", 3), ("434678", 16, "VISA", "CREDIT", 3),
        ("435376", 16, "VISA", "CREDIT", 3), ("435393", 16, "VISA", "CREDIT", 3),
        ("436152", 16, "VISA", "CREDIT", 3), ("437546", 16, "VISA", "CREDIT", 3),
        ("442142", 16, "VISA", "CREDIT", 3), ("451104", 16, "VISA", "CREDIT", 3),
        ("457262", 16, "VISA", "CREDIT", 3),
        # HDFC VISA debit
        ("400914", 16, "VISA", "DEBIT", 3), ("403875", 16, "VISA", "DEBIT", 3),
        ("405988", 16, "VISA", "DEBIT", 3), ("408981", 16, "VISA", "DEBIT", 3),
        ("414098", 16, "VISA", "DEBIT", 3), ("415921", 16, "VISA", "DEBIT", 3),
        ("416021", 16, "VISA", "DEBIT", 3), ("416233", 16, "VISA", "DEBIT", 3),
        ("418219", 16, "VISA", "DEBIT", 3), ("421340", 16, "VISA", "DEBIT", 3),
        ("423975", 16, "VISA", "DEBIT", 3), ("427879", 16, "VISA", "DEBIT", 3),
        ("438624", 16, "VISA", "DEBIT", 3), ("440384", 16, "VISA", "DEBIT", 3),
        ("440899", 16, "VISA", "DEBIT", 3), ("442378", 16, "VISA", "DEBIT", 3),
        ("445002", 16, "VISA", "DEBIT", 3), ("453561", 16, "VISA", "DEBIT", 3),
        ("458280", 16, "VISA", "DEBIT", 3), ("458281", 16, "VISA", "DEBIT", 3),
        # HDFC Mastercard
        ("222700", 16, "MASTER_CARD", "DEBIT", 3), ("222848", 16, "MASTER_CARD", "DEBIT", 3),
        ("222943", 16, "MASTER_CARD", "DEBIT", 3), ("223406", 16, "MASTER_CARD", "DEBIT", 3),
        ("223487", 16, "MASTER_CARD", "DEBIT", 3), ("222703", 16, "MASTER_CARD", "CREDIT", 3),
        ("558818", 16, "MASTER_CARD", "CREDIT", 3),
        # ICICI / SBI / Axis
        ("421323", 16, "VISA", "DEBIT", 3), ("421630", 16, "VISA", "DEBIT", 3),
        ("447747", 16, "VISA", "CREDIT", 3), ("512622", 16, "MASTER_CARD", "CREDIT", 3),
        ("468805", 16, "VISA", "DEBIT", 3),
    ],
}

CARD_BIN_FALLBACK = "US"


def _pick_card_bin(country: str, used_bins: Optional[set] = None) -> tuple[str, int, str, str, int]:
    """按国家选 BIN; 未收录 -> US 通用池 + 警告; banned 不重复 (used_bins 会话内去重)。"""
    cc = (country or "").upper()
    pool = CARD_BINS.get(cc)
    if not pool:
        pool = CARD_BINS[CARD_BIN_FALLBACK]
        from loguru import logger
        logger.warning("card bin fallback US for {}", cc)
    candidates = [b for b in pool if not used_bins or b[0] not in used_bins]
    if not candidates:
        candidates = pool
    return random.choice(candidates)


def build_card_number(bin_choice: tuple[str, int, str, str, int]) -> dict:
    """按 BIN 元组生成卡号 (Luhn 校验位) + 有效期 + CVV。"""
    bin_prefix, length, issuer, product_class, cvv_len = bin_choice
    middle_len = length - len(bin_prefix) - 1
    partial = bin_prefix + "".join(str(random.randint(0, 9)) for _ in range(middle_len))
    number = partial + str(_luhn_check_digit(partial))
    month = random.randint(1, 12)
    year = date.today().year + random.randint(2, 5)
    cvv = "".join(str(random.randint(0, 9)) for _ in range(cvv_len))
    return {
        "number": number,
        "expiry": f"{month:02d}/{year}",
        "cvv": cvv,
        "issuer": issuer,
        "product_class": product_class,
        "bin": bin_prefix,
    }


def generate_country_card(country: str, used_bins: Optional[set] = None) -> dict:
    """国家化卡片数据 (与表单国家一致的 BIN 池)。"""
    return build_card_number(_pick_card_bin(country, used_bins))


def issuer_type_for(number: str) -> str:
    """PayPal CardIssuerType enum 推导 (含 JCB/TROY, 修正 35xx->AMEX / 9792->VISA 误判)。

    2026-08-14 补: 50 段 Maestro 归 MASTER_CARD; 6 段分 Discover(60/64/65) 与 MC(622 UnionPay/636-639)。
    """
    prefix2 = number[:2]
    prefix4 = number[:4]
    if prefix2 in {"35", "36"}:
        return "JCB"
    if prefix4 == "9792":
        return "TROY"
    if prefix2 in {"34", "37"}:
        return "AMEX"
    if prefix4 and "2221" <= prefix4 <= "2720":
        return "MASTER_CARD"
    if prefix2.isdigit() and "51" <= prefix2 <= "55":
        return "MASTER_CARD"
    if prefix2 == "50":
        return "MASTER_CARD"
    if prefix2 == "4":
        return "VISA"
    if prefix2 in {"60", "64", "65"}:
        return "DISCOVER"
    if prefix2[0] == "6":
        return "MASTER_CARD"
    return "VISA"


# =============================================================================
# 各国身份号生成器
# =============================================================================


def th_pin() -> str:
    """泰国 13 位公民号: 首位 1-8, 权重 13..2 mod-11, 校验位 = (11-s%11)%10。"""
    first = random.randint(1, 8)
    rest = "".join(str(random.randint(0, 9)) for _ in range(11))
    base = f"{first}{rest}"
    check = _mod11_check_digit_v2(base, list(range(13, 1, -1)))
    return base + str(check)


def ae_emirates_id() -> str:
    """阿联酋 Emirates ID 15 位: 784-YYYY-NNNNNNN-C, Luhn 校验。"""
    year = random.randint(1970, 2000)
    seq = random.randint(0, 9_999_999)
    base = f"784{year:04d}{seq:07d}"
    return base + str(_luhn_check_digit(base))


def kr_rrn() -> str:
    """韩国居民号 RRN 13 位: YYMMDD + G1-4 + 4位登记地 + 2位序列 + 校验位。
    权重 2,3,4,5,6,7,8,9,2,3,4,5 mod-11。"""
    dob = generate_dob(min_year=1970, max_year=1999, fmt="%y%m%d")
    gender = random.randint(1, 4)
    place = f"{random.randint(0, 99):02d}{random.randint(0, 99):02d}"
    serial = f"{random.randint(0, 99):02d}"
    base = f"{dob}{gender}{place}{serial}"
    return base + str(_mod11_check_digit_kr(base))


def br_cpf() -> str:
    """巴西 CPF 11 位 mod-11 双校验位 (与 models.generate_cpf 同名算法)。"""
    while True:
        digits = [random.randint(0, 9) for _ in range(9)]
        if not all(d == digits[0] for d in digits):
            break
    for _ in range(2):
        total = sum(d * (len(digits) + 1 - i) for i, d in enumerate(digits))
        check = 0 if total % 11 < 2 else 11 - (total % 11)
        digits.append(check)
    cpf = "".join(str(d) for d in digits)
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def za_id(gender: str = "F") -> str:
    """南非 13 位: YYMMDD + 4位序列(女<5000/男>=5000) + 0身份 + 8 + Luhn。"""
    dob = generate_dob(min_year=1970, max_year=1999, fmt="%y%m%d")
    seq = random.randint(0, 4999) if gender.upper().startswith("F") else random.randint(5000, 9999)
    base = f"{dob}{seq:04d}08"
    return base + str(_luhn_check_digit(base))


def ar_cuit() -> str:
    """阿根廷 CUIT/CUIL 11 位: 前缀 20/23/24/27(个人) + 8位DNI + mod-11。"""
    while True:
        prefix = random.choice(["20", "23", "24", "27"])
        dni = f"{random.randint(0, 9_999_999):08d}"
        check = _mod11_check_digit_ar(prefix + dni)
        if check is not None:
            return f"{prefix}{dni}{check}"


def vn_cccd() -> str:
    """越南 12 位 CCCD: 3位省码 + 1位性别/世纪 + 2位出生年 + 6位随机 (无校验位)。"""
    province = random.randint(1, 96)
    gender_century = random.choice(["0", "1", "2", "3"])
    yy = f"{random.randint(40, 99):02d}"
    rand6 = f"{random.randint(0, 999_999):06d}"
    return f"{province:03d}{gender_century}{yy}{rand6}"


def bh_cpr() -> str:
    """巴林 CPR 9 位: YYMM + 4位随机 + 校验位(官方未公开, 用 Luhn 占位)。"""
    dob = generate_dob(min_year=1970, max_year=1999, fmt="%y%m")
    seq = f"{random.randint(0, 9999):04d}"
    base = f"{dob}{seq}"
    return base + str(_luhn_check_digit(base))


def de_iban() -> str:
    """德国 IBAN: DE + 2位校验 + BLZ(8) + 账号(10), mod-97 校验。"""
    blz = f"{random.randint(10000000, 99999999)}"
    konto = f"{random.randint(0, 9_999_999_999):010d}"
    bban = blz + konto
    check = _mod97_iban_check_digits("DE", bban)
    return f"DE{check}{bban}"


def _mod11_weighted_check(base: str, weights: list[int]) -> int:
    """通用加权和 mod-11 校验位: sum(d_i*w_i) mod 11。"""
    total = sum(int(d) * w for d, w in zip(base, weights))
    return total % 11


# 日本 My Number (個人番号) 12 位: 前 11 位随机, 权重 6,5,4,3,2,7,6,5,4,3,2,
# Q=sum mod 11, 校验位 = 0 if Q<=1 else (11-Q)。
# 官方算法: 内閣官房「行政手続における特定の個人を識別するための番号の利用等に関する法律」
# 第4/5条; (11-Q) mod 10 等价 (若 Q<=1 取 0)。已内置自验证 (见 _verify_jp_mynumber)。
def jp_mynumber() -> str:
    """日本 My Number 12 位: 权重 6,5,4,3,2,7,6,5,4,3,2, Q mod 11, 校验=(11-Q) if Q>1 else 0。"""
    base = "".join(str(random.randint(0, 9)) for _ in range(11))
    weights = [6, 5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    q = _mod11_weighted_check(base, weights)
    check = 0 if q <= 1 else 11 - q
    return base + str(check)


def _verify_jp_mynumber(number: str) -> bool:
    """自验证 My Number 校验位正确 (官方规则)。"""
    if len(number) != 12 or not number.isdigit():
        return False
    base, check = number[:11], int(number[11])
    q = _mod11_weighted_check(base, [6, 5, 4, 3, 2, 7, 6, 5, 4, 3, 2])
    return (0 if q <= 1 else 11 - q) == check


# 台湾身分證統一編號 10 位: 1 大写字母 + 9 数字 (末位为校验位)。字母映射两位数
# (A=10,B=11,...,I=34,J=18,K=19,...,Z=33; 字母表顺序但 I/J/O/W/X/Y 有偏移),
# 校验和 = a*1 + b*9 + d1*8 + d2*7 + ... + d8*1 (10 个权重对应 [a,b,d1..d8]),
# 校验位 d9 = (10 - sum%10) % 10。官方: 內政部戶政司「國民身分證統一編號檢查校驗位」。
def tw_national_id() -> str:
    """台湾身分證 10 位: 字母+8数字+校验位, 权重 1,9,8,7,6,5,4,3,2,1, 校验=(10-sum%10)%10。"""
    # 字母 -> 两位数映射 (官方表, A..Z 顺序, I=34/J=18/O=35/W=32 是已知偏移)。
    tw_letter_map = {
        "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16,
        "H": 17, "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22,
        "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28,
        "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
    }
    letter = random.choice(list(tw_letter_map.keys()))
    n1, n2 = divmod(tw_letter_map[letter], 10)
    # 性别位: 1=男, 2=女 (现行首位 1/2 区分性别), 后 7 位随机 d2..d8。
    gender = random.choice(["1", "2"])
    rest7 = "".join(str(random.randint(0, 9)) for _ in range(7))
    # d1..d8 = 性别位 + 7 随机 (共 8 位, 校验和用这 8 位 + 字母两位)。
    d8 = gender + rest7  # 8 位: d1..d8
    weights = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1]  # 对应 [a, b, d1..d8] (10 个)
    parts = [n1, n2] + [int(d) for d in d8]
    total = sum(p * w for p, w in zip(parts, weights))
    check = (10 - (total % 10)) % 10
    return f"{letter}{d8}{check}"  # 字母 + 8位 + 校验位 = 10


def _verify_tw_national_id(tid: str) -> bool:
    """自验证台湾身分證校验位正确 (官方规则)。"""
    if len(tid) != 10 or not tid[0].isalpha() or not tid[1:].isdigit():
        return False
    tw_letter_map = {
        "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16,
        "H": 17, "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22,
        "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28,
        "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
    }
    letter = tid[0].upper()
    if letter not in tw_letter_map:
        return False
    n1, n2 = divmod(tw_letter_map[letter], 10)
    d8 = tid[1:9]  # d1..d8 (校验位 d9 之前 8 位)
    weights = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1]  # 对应 [a, b, d1..d8]
    parts = [n1, n2] + [int(d) for d in d8]
    total = sum(p * w for p, w in zip(parts, weights))
    return (10 - (total % 10)) % 10 == int(tid[9])


# 德国 Steueridentifikationsnummer (税号 IdNr) 11 位: 前 10 位数字 + 1 位 mod-11 校验位。
# 权重 2,3,4,5,6,7,8,9,10,11, 校验位 = (11 - sum%11) mod 11 (若余 10 则非法, 重生成)。
# 官方: Bundeszentralamt für Steuern; 见 § 139b Abgabenordnung。
def de_steuer_id() -> str:
    """德国 Steuer-ID 11 位: 权重 2..11, mod-11 校验; 余 10 非法重试。"""
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    for _ in range(100):
        base = "".join(str(random.randint(0, 9)) for _ in range(10))
        s = sum(int(d) * w for d, w in zip(base, weights))
        rem = s % 11
        if rem == 10:
            continue  # 校验位非法 (10 无单字符), 重生成
        check = (11 - rem) % 11
        if check == 10:
            continue
        return base + str(check)
    # 兜底: 强制选一个余 0 的 base (校验位 = 0)。
    base = "0000000000"
    return base + "0"


def _verify_de_steuer_id(sid: str) -> bool:
    """自验证德国 Steuer-ID 校验位正确 (官方 mod-11)。"""
    if len(sid) != 11 or not sid.isdigit():
        return False
    base, check = sid[:10], int(sid[10])
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    s = sum(int(d) * w for d, w in zip(base, weights))
    rem = s % 11
    if rem == 10:
        return False
    return (11 - rem) % 11 == check


# 安哥拉 BI (Bilhete de Identidade) 9 位: 结构为省码前缀 + 序号, 官方未公开校验位算法。
# 与 BH CPR 同策略 (官方校验未公开仅格式): 生成 9 位结构合法号, 不附加伪校验位。
def ao_bi() -> str:
    """安哥拉 BI 9 位: 省码(1) + 序号(8); 官方无公开校验, 仅保证 9 位格式合法。"""
    province = random.randint(1, 9)  # Luanda=1 等 (省码占位)
    seq = f"{random.randint(0, 99_999_999):08d}"
    return f"{province}{seq}"


def _verify_ao_bi(bi: str) -> bool:
    """自验证安哥拉 BI 格式合法 (9 位数字; 官方校验未公开仅查格式)。"""
    return len(bi) == 9 and bi.isdigit()


def _curp_value(ch: str) -> int:
    return int(ch) if ch.isdigit() else ord(ch) - 55


def _curp_internal_consonant(word: str, exclude: str) -> str:
    for ch in word[1:]:
        if ch not in "AEIOU" and ch not in exclude:
            return ch
    return "X"


def mx_curp(names: tuple[str, str, str], dob: str, gender: str = "H", state: str = "DF") -> str:
    """墨西哥 CURP 18 字符: 姓1首字母+首内元音+姓2首字母+名首字母+YYMMDD+性别+州码
    +3个内辅音+同码(00后字母)+base37 mod-10 校验。names=(primerAP, segundoAP, nombre)。"""
    p1, p2, n = names
    vowels = "AEIOU"
    first = p1[0]
    vowel = next((c for c in p1[1:] if c in vowels), "X")
    second = p2[0] if p2 else "X"
    given = n[0]
    yymmdd = dob
    cons = (
        _curp_internal_consonant(p1, first)
        + _curp_internal_consonant(p2, second)
        + _curp_internal_consonant(n, given)
    )
    homo = random.choice("0123456789") if int(yymmdd[:2]) < 20 else random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    partial = f"{first}{vowel}{second}{given}{yymmdd}{gender}{state}{cons}{homo}"
    total = sum(_curp_value(ch) * (18 - i) for i, ch in enumerate(partial))
    check = (10 - (total % 10)) % 10
    return partial + str(check)


# =============================================================================
# 日本片假名 (Katakana) 生成: 罗马字规则转换 (일반 외국인명 → カタカナ)
# =============================================================================

_KATAKANA_MAP = {
    "a": "ア", "i": "イ", "u": "ウ", "e": "エ", "o": "オ",
    "ka": "カ", "ki": "キ", "ku": "ク", "ke": "ケ", "ko": "コ",
    "sa": "サ", "shi": "シ", "su": "ス", "se": "セ", "so": "ソ",
    "ta": "タ", "chi": "チ", "tsu": "ツ", "te": "テ", "to": "ト",
    "na": "ナ", "ni": "ニ", "nu": "ヌ", "ne": "ネ", "no": "ノ",
    "ha": "ハ", "hi": "ヒ", "fu": "フ", "he": "ヘ", "ho": "ホ",
    "ma": "マ", "mi": "ミ", "mu": "ム", "me": "メ", "mo": "モ",
    "ya": "ヤ", "yu": "ユ", "yo": "ヨ",
    "ra": "ラ", "ri": "リ", "ru": "ル", "re": "レ", "ro": "ロ",
    "wa": "ワ", "wo": "ヲ", "n": "ン",
    "ga": "ガ", "gi": "ギ", "gu": "グ", "ge": "ゲ", "go": "ゴ",
    "za": "ザ", "ji": "ジ", "zu": "ズ", "ze": "ゼ", "zo": "ゾ",
    "da": "ダ", "de": "デ", "do": "ド",
    "ba": "バ", "bi": "ビ", "bu": "ブ", "be": "ベ", "bo": "ボ",
    "pa": "パ", "pi": "ピ", "pu": "プ", "pe": "ペ", "po": "ポ",
    "kya": "キャ", "kyu": "キュ", "kyo": "キョ",
    "sha": "シャ", "shu": "シュ", "sho": "ショ",
    "cha": "チャ", "chu": "チュ", "cho": "チョ",
    "nya": "ニャ", "nyu": "ニュ", "nyo": "ニョ",
    "hya": "ヒャ", "hyu": "ヒュ", "hyo": "ヒョ",
    "mya": "ミャ", "myu": "ミュ", "myo": "ミョ",
    "rya": "リャ", "ryu": "リュ", "ryo": "リョ",
    "gya": "ギャ", "gyu": "ギュ", "gyo": "ギョ",
    "ja": "ジャ", "ju": "ジュ", "jo": "ジョ",
    "bya": "ビャ", "byu": "ビュ", "byo": "ビョ",
    "pya": "ピャ", "pyu": "ピュ", "pyo": "ピョ",
}


def latin_to_katakana(name: str) -> str:
    """英文名 → 片假名 (贪心最长匹配, 无映射字符近似)。"""
    name = name.lower().replace("-", " ").split(" ")[0]
    out = []
    i = 0
    while i < len(name):
        matched = False
        for ln in (3, 2, 1):
            frag = name[i : i + ln]
            if frag in _KATAKANA_MAP:
                out.append(_KATAKANA_MAP[frag])
                i += ln
                matched = True
                break
        if not matched:
            out.append("ッ")
            i += 1
    # 小さい長音化しない: 末尾のー付けはしない (姓名カナは通常そのまま)
    return "".join(out)


# =============================================================================
# 各国姓名池 (firstName/lastName)
# =============================================================================

_COUNTRY_NAMES: dict[str, tuple[list[str], list[str]]] = {
    "US": (
        ["James", "John", "Robert", "Michael", "William", "David", "Daniel", "Emily", "Anna", "Olivia", "Sarah", "Emma"],
        ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Wilson", "Taylor"],
    ),
    "JP": (
        ["Haruto", "Sota", "Yuto", "Riku", "Minato", "Yamato", "Sakura", "Yui", "Hana", "Aoi", "Mei", "Rin", "Kaito", "Ren"],
        ["Sato", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito", "Yamamoto", "Nakamura", "Kobayashi", "Kato"],
    ),
    "GB": (
        ["Oliver", "George", "Harry", "Jack", "Jacob", "Charlie", "Thomas", "Amelia", "Olivia", "Isla", "Poppy", "Emily"],
        ["Smith", "Jones", "Taylor", "Williams", "Brown", "Davies", "Evans", "Wilson", "Thomas", "Roberts"],
    ),
    "MX": (
        ["Juan", "Carlos", "Miguel", "Jose", "Luis", "Fernando", "Maria", "Guadalupe", "Sofia", "Carmen", "Ana", "Paola"],
        ["Hernandez", "Garcia", "Martinez", "Lopez", "Gonzalez", "Perez", "Rodriguez", "Sanchez", "Ramirez", "Cruz"],
    ),
    "TW": (
        ["Wei", "Ming", "Jia", "Jun", "Hao", "Yu", "Ting", "Yi", "Chen", "Wei", "Ling", "Hui", "Chih", "Hsin"],
        ["Chen", "Lin", "Huang", "Chang", "Li", "Wang", "Wu", "Liu", "Yang", "Tsai"],
    ),
    "TH": (
        ["Somchai", "Somsak", "Somporn", "Anan", "Panya", "Kittisak", "Malee", "Suda", "Nongyao", "Kanokwan", "Wilai", "Pornthip"],
        ["Saetang", "Saetia", "Saeteo", "Thongchai", "Srisuk", "Chairat", "Khamsaen", "Boonsong", "Jaroen", "Preecha"],
    ),
    "NL": (
        ["Daan", "Sem", "Lucas", "Finn", "Levi", "Bram", "Emma", "Sophie", "Mila", "Julia", "Saar", "Lieke"],
        ["De Jong", "Jansen", "De Vries", "Van den Berg", "Van Dijk", "Bakker", "Visser", "Smit", "Mulder", "De Boer"],
    ),
    "VN": (
        ["Nguyen", "Tran", "Minh", "Nam", "Duc", "Hieu", "Hung", "Linh", "Hoa", "Mai", "Lan", "Thu", "Hanh"],
        ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Phan", "Vu", "Dang", "Bui", "Do"],
    ),
    "BH": (
        ["Mohammed", "Ahmed", "Ali", "Hassan", "Husain", "Khalid", "Sara", "Fatima", "Aisha", "Maryam", "Noura", "Zainab"],
        ["Al Khalifa", "Al Sayed", "Al Arrayed", "Abdulla", "Al Mulla", "Al Qassimi", "Karimi", "Buzar", "Kanoo", "Fakhro"],
    ),
    "AO": (
        ["Joao", "Jose", "Manuel", "Carlos", "Antonio", "Pedro", "Maria", "Ana", "Fatima", "Isabel", "Luisa", "Teresa"],
        ["Dos Santos", "Fernandes", "Goncalves", "Pereira", "Rodrigues", "Lopes", "Da Silva", "Martins", "Sousa", "Almeida"],
    ),
    "AE": (
        ["Mohammed", "Ahmed", "Omar", "Abdullah", "Khalid", "Hamdan", "Fatima", "Aisha", "Mariam", "Noura", "Shaikha", "Amna"],
        ["Al Maktoum", "Al Nahyan", "Al Hashimi", "Al Marri", "Al Mazrouei", "Al Mansoori", "Al Shamsi", "Al Zaabi", "Alnuaimi", "Al Blooshi"],
    ),
    "AU": (
        ["Oliver", "William", "Jack", "Noah", "Henry", "Liam", "Charlotte", "Ruby", "Matilda", "Mia", "Chloe", "Zoe"],
        ["Smith", "Jones", "Williams", "Brown", "Wilson", "Taylor", "Thomas", "Johnson", "White", "Martin"],
    ),
    "CI": (
        ["Kouame", "Koffi", "Yao", "N'Guessan", "Kone", "Bamba", "Aya", "Aminata", "Fatou", "Mariam", "Adjoua", "Constance"],
        ["Kouame", "Kone", "Traore", "Bamba", "Coulibaly", "Diarra", "Sangare", "Ouattara", "N'Diaye", "Sylla"],
    ),
    "TR": (
        ["Mehmet", "Mustafa", "Ahmet", "Ali", "Emre", "Huseyin", "Ayse", "Fatma", "Zeynep", "Elif", "Merve", "Esra"],
        ["Yilmaz", "Kaya", "Demir", "Celik", "Sahin", "Yildiz", "Aydin", "Ozturk", "Arslan", "Dogan"],
    ),
    "DE": (
        ["Alexander", "Max", "Paul", "Jonas", "Leon", "Felix", "Lukas", "Anna", "Marie", "Sophie", "Laura", "Julia"],
        ["Muller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Hoffmann", "Schulz"],
    ),
    "BR": (
        ["Joao", "Pedro", "Lucas", "Mateus", "Gabriel", "Rafael", "Maria", "Ana", "Julia", "Larissa", "Camila", "Beatriz"],
        ["Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves", "Pereira", "Lima", "Gomes"],
    ),
    "KR": (
        ["Minjun", "Seojun", "Dohyun", "Junseo", "Jiho", "Hyunwoo", "Seoyeon", "Jiwon", "Minseo", "Eunji", "Sujin", "Hana"],
        ["Kim", "Lee", "Park", "Choi", "Jung", "Kang", "Cho", "Yoon", "Jang", "Lim"],
    ),
    "RU": (
        ["Alexander", "Dmitry", "Sergey", "Andrey", "Ivan", "Maxim", "Anastasia", "Maria", "Elena", "Olga", "Tatiana", "Natalia"],
        ["Ivanov", "Smirnov", "Kuznetsov", "Popov", "Vasilyev", "Petrov", "Sokolov", "Mikhailov", "Novikov", "Fyodorov"],
    ),
    "IN": (
        ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Reyansh", "Diya", "Aadhya", "Anaya", "Saanvi", "Myra", "Ishita"],
        ["Sharma", "Verma", "Gupta", "Kumar", "Singh", "Patel", "Shah", "Reddy", "Nair", "Iyer"],
    ),
    "SA": (
        ["Mohammed", "Abdullah", "Fahad", "Saad", "Khalid", "Turki", "Noura", "Sara", "Lama", "Reem", "Alia", "Jana"],
        ["Al Otaibi", "Al Ghamdi", "Al Harbi", "Al Zahrani", "Al Dossari", "Al Qahtani", "Al Anazi", "Al Mutairi", "Al Shammari", "Al Rashed"],
    ),
    "AR": (
        ["Juan", "Jose", "Carlos", "Pedro", "Martin", "Diego", "Sofia", "Valentina", "Camila", "Martina", "Julieta", "Agustina"],
        ["Gonzalez", "Rodriguez", "Gomez", "Fernandez", "Lopez", "Diaz", "Martinez", "Perez", "Romero", "Alvarez"],
    ),
    "ZA": (
        ["Johannes", "Pieter", "Thabo", "Sipho", "Lungile", "Naledi", "Thandi", "Nomvula", "Zanele", "Ayanda", "Kabelo", "Refilwe"],
        ["Botha", "Van der Merwe", "Nkosi", "Mokoena", "Dlamini", "Naidoo", "Khumalo", "Sithole", "Mahlangu", "Nel"],
    ),
    "HK": (
        ["Ka Ho", "Wing Yin", "Hoi Tung", "Chun Ming", "Ka Wai", "Man Hei", "Yuet Ching", "Tsz Yan", "Wai Sum", "Hiu Tung", "Ching Man", "Kwun Ho"],
        ["Chan", "Lee", "Cheung", "Ho", "Wong", "Ng", "Tam", "Yuen", "Tsang", "Lau"],
    ),
}

# kycFields 层: 该国家出现在 bundle 的 kycFields 映射, 不显示的字段不采集
_COUNTRY_FIELD_OVERRIDES: dict[str, list[str]] = {
    "US": [],
    "JP": ["Nationality", "DateOfBirth"],
    "MX": ["DateOfBirth"],
    "AU": ["DateOfBirth"],
    "IN": ["Nationality"],
    "CA": ["DateOfBirth", "Occupation"],
    "BR": ["DateOfBirth", "IdentityDocumentType", "IdentityDocumentNumber"],
    "C2": ["DateOfBirth", "IdentityDocumentType", "IdentityDocumentNumber"],
    "CH": ["DateOfBirth", "IdentityDocumentType", "IdentityDocumentNumber"],
    "IL": ["DateOfBirth", "IdentityDocumentType", "IdentityDocumentNumber"],
    "HK": ["DateOfBirth", "Gender", "PlaceOfBirth", "Nationality", "IdentityDocumentType", "IdentityDocumentNumber"],
    "RU": ["CountryOfResidence", "IdentityDocumentType", "IdentityDocumentNumber", "DateOfBirth",
           "SecondaryIdentityDocumentType", "SecondaryIdentityDocumentNumber"],
    "TH": ["DateOfBirth", "Nationality", "IdentityDocumentType", "IdentityDocumentNumber"],
    # TW 台湾: 完整 KYC (DateOfBirth/Nationality/身分證統一編號), 与 _FULL_KYC_COUNTRIES 对齐显式登记。
    "TW": ["DateOfBirth", "Nationality", "IdentityDocumentType", "IdentityDocumentNumber"],
}

_DEFAULT_KYC_FIELDS = ["DateOfBirth", "Nationality"]
_FULL_KYC_FIELDS = [
    "DateOfBirth", "Nationality", "IdentityDocumentType", "IdentityDocumentNumber",
]

_ID_TYPE_ALWAYS = ["NATIONAL_ID", "PASSPORT_NUMBER", "DRIVERS_LICENSE"]
_ID_TYPE_BY_COUNTRY: dict[str, list[str]] = {
    "BR": ["CPF"],
    "KR": ["PASSPORT_NUMBER", "DRIVERS_LICENSE"],
    "RU": ["PASSPORT_NUMBER"],
    "TH": ["NATIONAL_ID"],
    "HK": ["NATIONAL_ID", "PASSPORT_NUMBER", "TEMPORARY_NATIONAL_ID"],
    "AE": ["NATIONAL_ID"],
    "VN": ["NATIONAL_ID"],
    "BH": ["NATIONAL_ID"],
    "AR": ["NATIONAL_ID"],
    "ZA": ["NATIONAL_ID"],
    # MX: CURP 18 字符 (base37 mod-10 校验, 见 mx_curp); 身份证类目用 CURP 占位。
    "MX": ["CURP"],
    # TW: 国民身分证 10 位 (字母+9数字+校验位, 见 tw_national_id)。
    "TW": ["NATIONAL_ID"],
}


@dataclass
class CountryIdentity:
    country_code: str
    first_name: str
    last_name: str
    email: str
    password: str
    dob: str
    nationality: str = ""
    middle_name: str = ""
    kana_first: str = ""
    kana_last: str = ""
    identity_document_type: str = ""
    identity_document_number: str = ""
    crs_tax_details: list[dict] = field(default_factory=list)
    address: dict = field(default_factory=dict)      # line1/line2/city/state/postal_code
    phone_country: str = ""                          # "+1" / "+66" ...
    phone_number: str = ""                           # 完整号码含国码
    gender: str = ""                                 # "MALE"/"FEMALE" (HK 等需要)
    place_of_birth: str = ""                         # ISO2 国家码 (HK 等需要)
    occupation: str = ""                             # 职业枚举 (CA 等需要)
    secondary_identity_document: dict = field(default_factory=dict)  # RU 等需要次证件
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "country_code": self.country_code,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "password": self.password,
            "dob": self.dob,
            "nationality": self.nationality,
            "middle_name": self.middle_name,
            "kana_first": self.kana_first,
            "kana_last": self.kana_last,
            "identity_document_type": self.identity_document_type,
            "identity_document_number": self.identity_document_number,
            "crs_tax_details": self.crs_tax_details,
            "address": self.address,
            "phone_country": self.phone_country,
            "phone_number": self.phone_number,
            "gender": self.gender,
            "place_of_birth": self.place_of_birth,
            "occupation": self.occupation,
            "secondary_identity_document": self.secondary_identity_document,
            "extra": self.extra,
        }


@dataclass
class CountryProfile:
    code: str
    fields: list[str]
    id_types: list[str]
    generator: Callable[[], CountryIdentity]
    source: str = ""


_REGISTRY: dict[str, CountryProfile] = {}


def _make_profile(
    code: str,
    fields: list[str],
    id_types: list[str],
    gen: Callable[[], CountryIdentity],
    source: str,
) -> CountryProfile:
    return CountryProfile(code=code, fields=fields, id_types=id_types, generator=gen, source=source)


def _build_profile(country: str, name_pool: tuple[list[str], list[str]], fields: list[str],
                   id_types: list[str], source: str) -> CountryProfile:
    firsts, lasts = name_pool

    def gen() -> CountryIdentity:
        first = random.choice(firsts)
        last = random.choice(lasts)
        phone_prefix, phone_full = generate_country_phone(country)
        ident = CountryIdentity(
            country_code=country,
            first_name=first,
            last_name=last,
            email=generate_email(first, last, country=country),
            password=generate_password(),
            dob=generate_dob(),
            nationality=country,
            address=generate_country_address(country),
            phone_country=phone_prefix,
            phone_number=phone_full,
        )
        if "Nationality" not in fields:
            ident.nationality = ""
        if "IdentityDocumentType" in fields:
            itype = random.choice(id_types) if id_types else "PASSPORT_NUMBER"
            ident.identity_document_type = itype
            # MX CURP 需姓名/dob/性别/州 (上下文), 在此直接拼 (mx_curp 需多参)。
            if country == "MX" and itype == "CURP":
                # CURP 姓1=姓, 姓2=次姓(MX 习惯双姓, 这里用空次姓回退 X), 名=名。
                ident.identity_document_number = _mx_curp_from_ident(ident)
            else:
                ident.identity_document_number = _gen_doc_number(country, itype)
        if country == "JP":
            ident.kana_first = latin_to_katakana(ident.first_name)
            ident.kana_last = latin_to_katakana(ident.last_name)
        # 国家特殊 KYC 字段 (HK: Gender/PlaceOfBirth; RU: SecondaryIdentityDocument;
        # CA: Occupation)。仅在该字段出现在 kycFields 时才生成, 避免对不需要的国家
        # 发送冗余字段 (PayPal 校验 kycFields 白名单, 多发会 GRAPHQL_VALIDATION_FAILED)。
        if "Gender" in fields:
            ident.gender = random.choice(["MALE", "FEMALE"])
        if "PlaceOfBirth" in fields:
            # 出生地用国家 ISO2 (本地出生最常见; country_fields.json 未给具体城市级)。
            ident.place_of_birth = country
        if "Occupation" in fields:
            # CA 等需要职业枚举 (PayPal Occupation enum 子集, 选稳定常见值)。
            ident.occupation = random.choice([
                "ENGINEER", "TEACHER", "MANAGER", "ACCOUNTANT", "DESIGNER",
                "CONSULTANT", "SALES", "RETIRED", "STUDENT", "OTHER",
            ])
        if "SecondaryIdentityDocumentType" in fields and "SecondaryIdentityDocumentNumber" in fields:
            # RU 唯一带次级证件: TAX_IDENTIFICATION_NUMBER (ИИП) 或 PENSION_FUND_ID (СНИЛС)。
            sec_type = random.choice(["TAX_IDENTIFICATION_NUMBER", "PENSION_FUND_ID"])
            if sec_type == "TAX_IDENTIFICATION_NUMBER":
                # 俄罗斯 ИНН 12 位 (个人): 权重 7,2,4,10,3,5,9,4,6,8 mod-11 前 10 位,
                # 再权重 3,7,2,4,10,3,5,9,4,6,8 mod-11 全 11 位。
                base10 = "".join(str(random.randint(0, 9)) for _ in range(10))
                w1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
                c1 = (sum(int(d) * w for d, w in zip(base10, w1)) % 11) % 10
                base11 = base10 + str(c1)
                w2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
                c2 = (sum(int(d) * w for d, w in zip(base11, w2)) % 11) % 10
                sec_num = base11 + str(c2)
            else:
                # СНИЛС 11 位: 前 9 位 + 2 校验位。校验和 = sum(d_i * (9-i)) for i in 0..8。
                # 官方规则: check = sum % 101; 若 check == 100 则校验位 = 00 (其余 <100 直写)。
                base9 = "".join(str(random.randint(0, 9)) for _ in range(9))
                s = sum(int(d) * (9 - i) for i, d in enumerate(base9))
                check = s % 101
                if check == 100:
                    check = 0  # 100 -> 00 (官方映射)
                sec_num = base9 + f"{check:02d}"
            ident.secondary_identity_document = {"type": sec_type, "value": sec_num}
        return ident

    return _make_profile(country, fields, id_types, gen, source)


# MX CURP 上下文拼接: 需姓名(doble apellido), dob(YYMMDD), 性别(H/M), 州码(2字母)。
# names=(primerApellido, segundoApellido, nombre); 无次姓用空串(内部回退 X)。
_MX_CURP_STATES = ["DF", "NL", "JL", "MX", "BC", "BS", "SO", "MI", "GT", "QR"]
_MX_CURP_GENDER = ["H", "M"]


def _mx_curp_from_ident(ident: "CountryIdentity") -> str:
    """从已生成的身份拼墨西哥 CURP (姓名/dob/性别/州随机)。"""
    primer_ap = (ident.last_name or "").upper().replace(" ", "")
    nombre = (ident.first_name or "").upper().replace(" ", "")
    # dob 格式 dd/mm/yyyy -> yymmdd
    dob = ident.dob or ""
    try:
        d, m, y = dob.split("/")
        yymmdd = f"{y[2:]}{m}{d}"
    except Exception:
        yymmdd = "".join(random.choices("0123456789", k=6))
    gender = random.choice(_MX_CURP_GENDER)
    state = random.choice(_MX_CURP_STATES)
    return mx_curp((primer_ap, "", nombre), yymmdd, gender, state)


def _gen_doc_number(country: str, doc_type: str) -> str:
    if country == "TH":
        return th_pin()
    if country == "BR" and doc_type == "CPF":
        return br_cpf()
    if country == "AE":
        return ae_emirates_id()
    if country == "KR":
        return kr_rrn() if doc_type == "NATIONAL_ID" else f"{random.randint(0, 9_999_999):08d}"
    if country == "AR":
        return ar_cuit()
    if country == "VN":
        return vn_cccd()
    if country == "BH":
        return bh_cpr()
    if country == "ZA":
        return za_id()
    if country == "TW":
        return tw_national_id()  # 台湾身分證 10 位 (字母+9数字+校验位)
    if country == "JP" and doc_type in {"MY_NUMBER", "INDIVIDUAL_NUMBER", "NATIONAL_ID"}:
        # JP My Number 12 位 (校验位算法), 仅当证件类目要求时提供。
        return jp_mynumber()
    if country == "DE" and doc_type in {"TAX_IDENTIFICATION_NUMBER", "TAX_ID", "STEUER_ID"}:
        # 德国 Steuer-ID 11 位 (mod-11 校验)。
        return de_steuer_id()
    if country == "AO":
        return ao_bi()  # 安哥拉 BI 9 位 (官方校验未公开仅格式)
    return "".join(str(random.randint(0, 9)) for _ in range(9))


def _resolve_fields(country: str) -> list[str]:
    overrides = _COUNTRY_FIELD_OVERRIDES.get(country)
    if overrides is not None:
        return overrides
    return _FULL_KYC_FIELDS if country in _FULL_KYC_COUNTRIES else _DEFAULT_KYC_FIELDS


def get_country_profile(country: str) -> CountryIdentity:
    """按国家生成完整表单身份数据 (覆盖 kycFields 配置)。"""
    country = country.upper()
    if country in _REGISTRY:
        return _REGISTRY[country].generator()

    if country not in _COUNTRY_NAMES:
        raise KeyError(f"未收录国家: {country}")

    fields = _resolve_fields(country)
    id_types = _ID_TYPE_BY_COUNTRY.get(country, _ID_TYPE_ALWAYS)
    profile = _build_profile(country, _COUNTRY_NAMES[country], fields, id_types, "registry")
    _REGISTRY[country] = profile
    return profile.generator()


_FULL_KYC_COUNTRIES = {
    "AE", "AD", "AR", "BH", "BM", "BS", "BW", "CL", "CO", "CR", "DO", "EC", "FO",
    "GE", "GL", "GT", "HN", "HR", "ID", "IS", "JM", "JO", "KE", "KR", "KW", "KY",
    "KZ", "LS", "MA", "MC", "MD", "MU", "MY", "MZ", "NI", "NZ", "OM", "PA", "PE",
    "PH", "QA", "RS", "SA", "SG", "SN", "SV", "TW", "UY", "VE", "VN", "ZA",
}


def generate_country_data(country: str, count: int = 1) -> list[dict]:
    """批量生成 (供 CLI/测试/API 使用)。"""
    return [get_country_profile(country).to_dict() for _ in range(count)]


def available_countries() -> list[str]:
    return sorted(_COUNTRY_NAMES.keys())


def profile_summary(country: str) -> dict:
    """返回该国家表单字段配置与 id types (供 api 暴露配置)。"""
    country = country.upper()
    if country not in _COUNTRY_NAMES:
        raise KeyError(country)
    profile = _REGISTRY.get(country) or _build_profile(
        country, _COUNTRY_NAMES[country],
        _resolve_fields(country),
        _ID_TYPE_BY_COUNTRY.get(country, _ID_TYPE_ALWAYS),
        "static",
    )
    return {
        "country": country,
        "fields": profile.fields,
        "id_types": profile.id_types,
        "source": profile.source,
    }


if __name__ == "__main__":
    import sys

    target = sys.argv[1].upper() if len(sys.argv) > 1 else "US"
    for cc in sorted(_COUNTRY_NAMES):
        if cc == target or target == "ALL":
            try:
                data = get_country_profile(cc)
                print(f"[{cc}] {data.first_name} {data.last_name} | {data.dob} | "
                      f"doc={data.identity_document_type or '-'}:{data.identity_document_number or '-'}")
            except Exception as exc:  # noqa: BLE001
                print(f"[{cc}] ERR {exc}")