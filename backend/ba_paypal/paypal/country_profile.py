"""国家上下文 CountryContext: 授权段唯一事实源 (提链国家 → 表单/指纹/接码/卡全联动)。

数据表覆盖提链可用 15 国 (BA_COUNTRY_ALIGN_PLAN_20260812 §三)。
时区偏移不存死值, 运行时用 zoneinfo 计算 (DST 漂移安全)。

使用方式:
    from paypal.country_profile import country_context, apply_profile_overrides
    ctx = country_context("TH")
    profile = apply_profile_overrides(dict(BROWSER_PROFILE), ctx)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

# =============================================================================
# 国家静态映射表 (提链可用 15 国)
# =============================================================================
# 字段: locale / language / timezone(IANA) / lang(2字母) / phone(+前缀) /
#        smsbower_id(数字国家码, 已 2026-08-12 用 getPricesV3 全 15 国实测回填) /
#        currency / proxy_supported(711 白名单) / sms_supported(全部实测有货)
_COUNTRY_MAP: dict[str, dict] = {
    "US": dict(locale="en_US", language="en-US", timezone="America/New_York", lang="en",
               phone="+1", smsbower_id="12", currency="USD", proxy_supported=True, sms_supported=True),
    "GB": dict(locale="en_GB", language="en-GB", timezone="Europe/London", lang="en",
               phone="+44", smsbower_id="16", currency="GBP", proxy_supported=True, sms_supported=True),
    "AU": dict(locale="en_AU", language="en-AU", timezone="Australia/Sydney", lang="en",
               phone="+61", smsbower_id="23", currency="AUD", proxy_supported=True, sms_supported=True),
    "DE": dict(locale="de_DE", language="de-DE", timezone="Europe/Berlin", lang="de",
               phone="+49", smsbower_id="22", currency="EUR", proxy_supported=True, sms_supported=True),
    "JP": dict(locale="ja_JP", language="ja-JP", timezone="Asia/Tokyo", lang="ja",
               phone="+81", smsbower_id="40", currency="JPY", proxy_supported=True, sms_supported=True),
    "TH": dict(locale="th_TH", language="th-TH", timezone="Asia/Bangkok", lang="th",
               phone="+66", smsbower_id="34", currency="THB", proxy_supported=True, sms_supported=True),
    "NL": dict(locale="nl_NL", language="nl-NL", timezone="Europe/Amsterdam", lang="nl",
               phone="+31", smsbower_id="15", currency="EUR", proxy_supported=True, sms_supported=True),
    "VN": dict(locale="vi_VN", language="vi-VN", timezone="Asia/Ho_Chi_Minh", lang="vi",
               phone="+84", smsbower_id="8", currency="VND", proxy_supported=True, sms_supported=True),
    "BH": dict(locale="ar_BH", language="en-BH", timezone="Asia/Bahrain", lang="en",
               phone="+973", smsbower_id="39", currency="BHD", proxy_supported=True, sms_supported=True),
    "AO": dict(locale="pt_AO", language="pt-AO", timezone="Africa/Luanda", lang="pt",
               phone="+244", smsbower_id="36", currency="AOA", proxy_supported=True, sms_supported=True),
    "AE": dict(locale="ar_AE", language="en-AE", timezone="Asia/Dubai", lang="en",
               phone="+971", smsbower_id="21", currency="AED", proxy_supported=True, sms_supported=True),
    "CI": dict(locale="fr_CI", language="fr-CI", timezone="Africa/Abidjan", lang="fr",
               phone="+225", smsbower_id="32", currency="XOF", proxy_supported=True, sms_supported=True),
    "TR": dict(locale="tr_TR", language="tr-TR", timezone="Europe/Istanbul", lang="tr",
               phone="+90", smsbower_id="27", currency="TRY", proxy_supported=True, sms_supported=True),
    "BR": dict(locale="pt_BR", language="pt-BR", timezone="America/Sao_Paulo", lang="pt",
               phone="+55", smsbower_id="73", currency="BRL", proxy_supported=True, sms_supported=True),
    "KR": dict(locale="ko_KR", language="ko-KR", timezone="Asia/Seoul", lang="ko",
               phone="+82", smsbower_id="14", currency="KRW", proxy_supported=True, sms_supported=True),
    # MX (墨西哥): SMSBower 国家码 73 见 getPricesV3 墨西哥行; 711 region=MX 实测可用。
    "MX": dict(locale="es_MX", language="es-MX", timezone="America/Mexico_City", lang="es",
               phone="+52", smsbower_id="73", currency="MXN", proxy_supported=True, sms_supported=True),
    # TW (台湾): SMSBower 数字国家码未实测, 标 "*" 推估 + sms_supported=False 防止误接码;
    # 711 region=TW 可用, 接码走 mx(73) 兜底 (smsbower_country_id 会 rstrip("*"))。
    "TW": dict(locale="zh_TW", language="zh-TW", timezone="Asia/Taipei", lang="zh",
               phone="+886", smsbower_id="73*", currency="TWD", proxy_supported=True, sms_supported=False),
}

# 接码实测价 (2026-08-12, service=ts, USD): BR 0.004 / VN 0.012 / KR 0.014 /
# GB 0.021 / US 0.024 / BH 0.09 / DE 0.124 / NL 0.142 / AO 0.142 / AU 0.187 /
# TH 0.187 / AE 0.187 / CI 0.187 / TR 0.187 / JP 0.357 — 预算建议 >= 0.05 起步
SMS_PRICE_DEFAULT = "0.05"

# 711 住宅代理支持近 200 国 (region 参数任意国家均构造可用链路, 且有 sing-box/QG 兜底),
# 不再用 10 国白名单 — 前端"无代理"置灰以 711 全集为准。
_SUPPORTED_COUNTRIES: frozenset[str] = frozenset({
    # 主用国家 (实测/常用)
    "US", "GB", "CA", "AU", "DE", "FR", "JP", "SG", "NL", "BR",
    # 711 region 已支持 (近 200 国中的常用子集, 其余国家同样可用)
    "TH", "VN", "BH", "AO", "AE", "CI", "TR", "KR", "MX", "ID", "PH",
    "MY", "IN", "PK", "BD", "LK", "NP", "HK", "TW", "CN", "MO", "KR",
    "AR", "CL", "CO", "PE", "UY", "PY", "EC", "VE", "BO", "CR", "PA",
    "DO", "GT", "HN", "NI", "SV", "JM", "TT", "CU",
    "BE", "AT", "CH", "IE", "IT", "ES", "PT", "SE", "NO", "DK", "FI",
    "PL", "CZ", "SK", "HU", "RO", "BG", "GR", "HR", "SI", "EE", "LV",
    "LT", "UA", "RU", "BY", "KZ", "UZ", "GE", "AM", "AZ", "MD", "RS",
    "BA", "MK", "AL", "IS", "LU", "MT", "CY",
    "ZA", "EG", "MA", "DZ", "TN", "NG", "GH", "KE", "TZ", "UG", "ET",
    "SN", "CM", "CD", "ZW", "ZM", "MZ", "MW", "BW", "NA", "MU", "SC",
    "SA", "QA", "KW", "OM", "JO", "LB", "IL", "IQ", "YE", "SY", "AF",
    "UZ", "TJ", "KG", "MN", "KH", "LA", "MM", "BT", "MV", "BN", "TL",
    "FJ", "PG", "NZ", "GU",
})

# 每国邮箱域名池 (域名即国家信号, 如 uol.com.br 不可用于他国)
_EMAIL_DOMAINS: dict[str, list[str]] = {
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
    # MX/TW 新增国家 (常用邮箱域名, hotmail/outlook 在拉美/台普及)。
    "MX": ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com.mx", "prodigy.net.mx", "icloud.com"],
    "TW": ["gmail.com", "yahoo.com.tw", "hotmail.com", "outlook.com", "icloud.com", "pchome.com.tw"],
}

# 每国地址池 (算法生成版): postal_spec 定义邮编格式规则, regions 把城市/州/街道
# 绑到该邮编前缀区, 保证 street↔postal 区域一致 (用邮编规则生成, 不再硬编码配对表)。
#
# postal_spec.kind:
#   "digits"   -> 固定长度纯数字; 用 prefix 区段 + 随机后缀 (length 总长度)。
#   "gb_post"  -> 英国邮编 AN\dN\s?NAA / AAN\s?NAA, prefix 给定外码区 (如 "E1")。
#   "nl_post"  -> 荷兰邮编 NNNN<space>LL, prefix 给 4 位数字外码, 2 位字母随机。
#   "jp_post"  -> 日本邮编 NNN-NNNN, prefix 给 3 位前, 4 位随机。
#   "br_cep"   -> 巴西 CEP NNNNN-NNN, prefix 给 5 位前, 3 位随机。
#   "ci_bp"    -> 科特迪瓦 "NN BP N" 邮政信箱格式, prefix 给 2 位区号。
#   "fixed"    -> 直接用 region 里给定的完整 postal (AO/AE/BH 等无统一邮编或自由邮编)。
# regions: list of {city, state, postal_prefix, streets[], line2_policy}
# line2_policy: "district"(巴西 bairro) / "apartment"(公寓号) / "empty"(无)
_ADDRESSES: dict[str, dict] = {
    # US: 5 位 ZIP, 纽约市 ZIP 100xx/101xx 区段 (曼哈顿)。
    "US": dict(postal_spec=dict(kind="digits", length=5), regions=[
        dict(city="New York", state="NY", postal_prefix="100", line2_policy="apartment",
             streets=("350 5th Ave", "215 W 34th St", "10 E 33rd St", "55 W 25th St")),
        dict(city="New York", state="NY", postal_prefix="101", line2_policy="apartment",
             streets=("200 Park Ave", "570 Lexington Ave", "60 Wall St", "40 Wall St")),
    ]),
    # GB: 伦敦东码区 E1, 邮政自动 AN NAA 格式 (E1 6AN 等), 全部落在 Tower Hamlets。
    "GB": dict(postal_spec=dict(kind="gb_post"), regions=[
        dict(city="London", state="London", postal_prefix="E1", line2_policy="apartment",
             streets=("6 Brushfield Street", "12 Osborn Street", "8 Wentworth Street", "3 Crispin Street")),
        dict(city="London", state="London", postal_prefix="E1", line2_policy="apartment",
             streets=("21 Brick Lane", "14 Commercial Street", "2 Whitechapel Road", "9 Hanbury Street")),
    ]),
    # AU: 4 位邮编, 悉尼 CBD 2000/2010 区段 (NSW)。
    "AU": dict(postal_spec=dict(kind="digits", length=4), regions=[
        dict(city="Sydney", state="NSW", postal_prefix="200", line2_policy="apartment",
             streets=("1 Macquarie St", "5 Martin Place", "100 George St", "25 Pitt St")),
        dict(city="Sydney", state="NSW", postal_prefix="201", line2_policy="apartment",
             streets=("10 Crown St", "88 Oxford St", "300 King St", "55 Darlinghurst Rd")),
    ]),
    # DE: 5 位 Postleitzahl, 柏林 Mitte 101xx (Invalidenstrasse 属 10115)。
    "DE": dict(postal_spec=dict(kind="digits", length=5), regions=[
        dict(city="Berlin", state="Berlin", postal_prefix="101", line2_policy="empty",
             streets=("Invalidenstrasse 1", "Unter den Linden 10", "Friedrichstrasse 44", "Torstrasse 88")),
        dict(city="Berlin", state="Berlin", postal_prefix="102", line2_policy="empty",
             streets=("Alexanderplatz 1", "Karl-Marx-Allee 84", "Frankfurter Tor 5", "Strausberger Platz 1")),
    ]),
    # JP: NNN-NNNN, 东京千代田 100-xxxx / 港区 150-xxxx / 中央区 104-xxxx / 新宿 160-xxxx。
    "JP": dict(postal_spec=dict(kind="jp_post"), regions=[
        dict(city="Tokyo", state="Tokyo", postal_prefix="100", line2_policy="empty",
             streets=("1-1 Chiyoda", "1-1 Marunouchi", "1-2 Otemachi", "2-1 Uchisaiwaicho")),
        dict(city="Tokyo", state="Tokyo", postal_prefix="150", line2_policy="empty",
             streets=("2-10-1 Ginza", "3-25-3 Shibuya", "4-8-1 Shinjuku", "5-2-1 Higashi")),
    ]),
    # TH: 5 位邮编, 曼谷 10xxx 区 (Sukhumvit/Silom 均属 10110/10500)。
    "TH": dict(postal_spec=dict(kind="digits", length=5), regions=[
        dict(city="Bangkok", state="Bangkok", postal_prefix="101", line2_policy="apartment",
             streets=("1 Sukhumvit Rd", "89 Silom Rd", "45 Phayathai Rd", "22 Ratchadamri Rd")),
        dict(city="Bangkok", state="Bangkok", postal_prefix="105", line2_policy="apartment",
             streets=("10 Sathorn Rd", "30 Rama IV Rd", "55 Wireless Rd", "18 Ploenchit Rd")),
    ]),
    # NL: NNNN LL, 阿姆斯特丹中心 1011-1016 区 (Damrak/Prinsengracht)。
    "NL": dict(postal_spec=dict(kind="nl_post"), regions=[
        dict(city="Amsterdam", state="Noord-Holland", postal_prefix="1011", line2_policy="apartment",
             streets=("Damrak 1", "Kalverstraat 20", "Rokin 10", "Spuistraat 5")),
        dict(city="Amsterdam", state="Noord-Holland", postal_prefix="1016", line2_policy="apartment",
             streets=("Leidseplein 8", "Prinsengracht 263", "Herengracht 100", "Keizersgracht 200")),
    ]),
    # VN: 6 位邮编 (胡志明市 70xxxx/72xxxx), 越南邮编为 6 位无强校验位。
    "VN": dict(postal_spec=dict(kind="digits", length=6), regions=[
        dict(city="Ho Chi Minh City", state="Ho Chi Minh City", postal_prefix="7000", line2_policy="apartment",
             streets=("1 Nguyen Hue", "10 Le Loi", "25 Dong Khoi", "88 Hai Ba Trung")),
        dict(city="Ho Chi Minh City", state="Ho Chi Minh City", postal_prefix="7200", line2_policy="apartment",
             streets=("5 Pham Van Dong", "30 Vo Van Kiet", "12 Tran Hung Dao", "50 Ly Tu Trong")),
    ]),
    # BH: 巴林无统一邮编, 用邮政信箱区码 (3xx), fixed 避免编造无效数字。
    "BH": dict(postal_spec=dict(kind="fixed"), regions=[
        dict(city="Manama", state="Manama", postal_prefix="317", line2_policy="apartment",
             streets=("1 Government Ave", "15 Diplomatic Area", "26 Salman Ave", "40 Hoora Rd")),
    ]),
    # AO: 安哥拉无统一邮编 (Luanda 用邮政信箱), fixed 用真实信箱区段。
    "AO": dict(postal_spec=dict(kind="fixed"), regions=[
        dict(city="Luanda", state="Luanda", postal_prefix="1855", line2_policy="apartment",
             streets=("1 Marginal", "12 Av 4 de Fevereiro", "30 Rua Amilcar Cabral", "55 Rua da Missao")),
    ]),
    # AE: 阿联酋无标准邮编 (官方建议 00000), digits 长度 5 但 prefix 000 退化为全 0。
    "AE": dict(postal_spec=dict(kind="digits", length=5, allow_all_zero=True), regions=[
        dict(city="Dubai", state="Dubai", postal_prefix="000", line2_policy="apartment",
             streets=("1 Sheikh Zayed Rd", "15 Jumeirah Beach Rd", "36 Al Wasl Rd", "70 Trade Centre Rd")),
    ]),
    # CI: 科特迪瓦 "NN BP N" 邮政信箱格式, Abidjan 各区 01-10。
    "CI": dict(postal_spec=dict(kind="ci_bp"), regions=[
        dict(city="Abidjan", state="Abidjan", postal_prefix="01", line2_policy="apartment",
             streets=("1 Ave de la Republique", "12 Rue du Commerce", "28 Bd de Marseille", "50 Rue des Jardins")),
    ]),
    # TR: 5 位邮编, 伊斯坦布尔欧洲侧 34xxxx (Beyoglu 344xx)。
    "TR": dict(postal_spec=dict(kind="digits", length=5), regions=[
        dict(city="Istanbul", state="Istanbul", postal_prefix="344", line2_policy="apartment",
             streets=("1 Istiklal Cd", "20 Bagdat Cd", "45 Barbaros Blv", "70 Ataturk Blv")),
        dict(city="Istanbul", state="Istanbul", postal_prefix="347", line2_policy="apartment",
             streets=("10 Levent Cd", "33 Etiler Mah", "77 Maslak Cd", "5 Nispetiye Cd")),
    ]),
    # KR: 5 位 우편번호 (旧制 NNNNN), 首尔 Jongno 045xx / Gangnam 060xx。
    "KR": dict(postal_spec=dict(kind="digits", length=5), regions=[
        dict(city="Seoul", state="Seoul", postal_prefix="045", line2_policy="apartment",
             streets=("1 Jong-ro", "12 Gangnam-daero", "30 Teheran-ro", "55 Yulgok-ro")),
        dict(city="Seoul", state="Seoul", postal_prefix="060", line2_policy="apartment",
             streets=("5 Gangnam-gu", "22 Apgujeong-ro", "8 Yeoksam-ro", "14 Seolleung-ro")),
    ]),
    # BR: CEP NNNNN-NNN, 圣保罗市中心 013xx/045xx (Av Paulista)。
    "BR": dict(postal_spec=dict(kind="br_cep"), regions=[
        dict(city="Sao Paulo", state="SP", postal_prefix="013", line2_policy="district",
             streets=("Av Paulista 1000", "Rua Augusta 2000", "Alameda Santos 1300", "Rua Bela Cintra 1500")),
        dict(city="Sao Paulo", state="SP", postal_prefix="045", line2_policy="district",
             streets=("Av Brigadeiro Faria Lima 3000", "Rua dos Pinheiros 500", "Av Luis Carlos Berrini 800", "Rua Joaquim Floriano 200")),
    ]),
    # MX: 5 位 Codigo Postal, 墨西哥城 CDMX 00xxx-01xxx 区 (Cuauhtemoc 06500/Benito Juarez 03xxx)。
    "MX": dict(postal_spec=dict(kind="digits", length=5), regions=[
        dict(city="Mexico City", state="CDMX", postal_prefix="065", line2_policy="apartment",
             streets=("Av Paseo de la Reforma 250", "Av Insurgentes Sur 1234", "Rio Guadalquivir 50", "Av Chapultepec 200")),
        dict(city="Mexico City", state="CDMX", postal_prefix="039", line2_policy="apartment",
             streets=("Av Patriotismo 100", "Calle Tlacotalpan 50", "Av Cuauhtemoc 300", "Calle Amsterdam 80")),
    ]),
    # TW: 3+3 邮编 (NNN), 台北市 100-116 区段 (中正区 100/大安区 106)。
    "TW": dict(postal_spec=dict(kind="digits", length=3), regions=[
        dict(city="Taipei", state="Taipei", postal_prefix="100", line2_policy="apartment",
             streets=("Zhongxiao E Rd Sec 1 100", "Zhongshan N Rd Sec 1 50", "Bade Rd Sec 1 80", "Xinyi Rd Sec 1 20")),
        dict(city="Taipei", state="Taipei", postal_prefix="106", line2_policy="apartment",
             streets=("Dunhua S Rd Sec 1 200", "Renai Rd Sec 1 150", "Zhongxiao E Rd Sec 4 300", "Heping E Rd Sec 1 60")),
    ]),
}


# Windows 无 tzdata 包时 zoneinfo 抛 KeyError, 用静态基准偏移兜底 (仅为失败降级, 不阻塞)
_TZ_OFFSET_FALLBACK: dict[str, int] = {
    "America/New_York": -300, "Europe/London": 0, "Australia/Sydney": 600,
    "Europe/Berlin": 60, "Asia/Tokyo": 540, "Asia/Bangkok": 420,
    "Europe/Amsterdam": 60, "Asia/Ho_Chi_Minh": 420, "Asia/Bahrain": 180,
    "Africa/Luanda": 60, "Asia/Dubai": 240, "Africa/Abidjan": 0,
    "Europe/Istanbul": 180, "America/Sao_Paulo": 180, "Asia/Seoul": 540,
    "America/Mexico_City": -360, "Asia/Taipei": 480,
}


def _tz_offset_minutes(tz_name: str, at: Optional[datetime] = None) -> int:
    """当前时刻的 UTC 偏移 (分钟)。优先 IANA+zoneinfo 运行时计算 (DST 自动), 失败回退静态值。"""
    try:
        zone = ZoneInfo(tz_name)
        dt = at or datetime.now(zone)
        offset = dt.utcoffset()
        return int(offset.total_seconds() // 60) if offset is not None else 0
    except Exception:
        return _TZ_OFFSET_FALLBACK.get(tz_name, 0)


def smsbower_country_id(country: str) -> str:
    """接码平台数字国家码 (含 * 推估值, 实施时用 getPricesV3 实测回填)。"""
    entry = _COUNTRY_MAP.get((country or "").upper())
    if not entry:
        raise KeyError(f"unsupported country: {country}")
    return str(entry["smsbower_id"]).rstrip("*")


def sms_supported(country: str) -> bool:
    entry = _COUNTRY_MAP.get((country or "").upper())
    if not entry:
        return False
    return bool(entry.get("sms_supported")) and not str(entry.get("smsbower_id") or "").endswith("*")


def proxy_supported(country: str) -> bool:
    c = (country or "").upper()
    return c in _SUPPORTED_COUNTRIES


def email_domains(country: str) -> list[str]:
    """按国家取邮箱域名池。优先用户配置 (email_domains.json), 回退内置默认。"""
    try:
        from core.email_domains_store import email_domains_store
        return email_domains_store.domains_for_country(country or "US")
    except Exception:
        return list(_EMAIL_DOMAINS.get((country or "").upper(), _EMAIL_DOMAINS["US"]))


def address_pool(country: str) -> dict:
    entry = _ADDRESSES.get((country or "").upper())
    if not entry:
        raise KeyError(f"unsupported country: {country}")
    return entry


# =============================================================================
# CountryContext
# =============================================================================


@dataclass(frozen=True)
class CountryContext:
    country: str                        # ISO2, 例如 "US" / "JP" / "TH"
    kyc_fields: list                    # 表单字段白名单
    id_types: list                      # 可选证件类型
    locale: str                         # "en_US" / "ja_JP" ...
    language: str                       # "en-US" / "ja-JP" ...
    timezone: str                       # IANA 名
    tz_offset_minutes: int              # 运行时计算 (DST 安全)
    lang: str                           # 2字母语言 (weasley/analytics)
    currency: str                       # 币种
    phone_country: str                  # 手机号国家前缀 "+1"
    sms_country_id: str                 # 接码平台数字国家码
    proxy_country: str                  # 代理 region (711 同名)
    proxy_supported: bool = True        # 711 直连可用性 (前端置灰用)
    sms_supported: bool = True          # 接码平台支持 (前端置灰用)
    extra: dict = field(default_factory=dict)


def country_context(country: str) -> CountryContext:
    """组装国家上下文。kyc_fields/id_types 懒加载自 identity_lib (避免循环导入)。"""
    cc = (country or "").strip().upper()
    entry = _COUNTRY_MAP.get(cc)
    if not entry:
        raise KeyError(f"unsupported country: {cc}")
    fields: list = []
    id_types: list = []
    try:
        from paypal.identity_lib import profile_summary
        summary = profile_summary(cc)
        fields = list(summary.get("fields") or [])
        id_types = list(summary.get("id_types") or [])
    except Exception:
        fields = ["DateOfBirth", "Nationality"]
        id_types = []
    return CountryContext(
        country=cc,
        kyc_fields=fields,
        id_types=id_types,
        locale=entry["locale"],
        language=entry["language"],
        timezone=entry["timezone"],
        tz_offset_minutes=_tz_offset_minutes(entry["timezone"]),
        lang=entry["lang"],
        currency=entry["currency"],
        phone_country=entry["phone"],
        sms_country_id=str(entry["smsbower_id"]).rstrip("*"),
        proxy_country=cc,
        proxy_supported=proxy_supported(cc),
        sms_supported=sms_supported(cc),
    )


def apply_profile_overrides(profile: dict, ctx: CountryContext) -> dict:
    """把国家上下文写进指纹 profile (BROWSER_PROFILE 派生副本)。

    只覆盖国家相关信号, 硬件信号 (GPU/webgl/deviceMemory...) 保留模板值。
    """
    out = dict(profile or {})
    out.update(
        country=ctx.country,
        language=ctx.language,
        locale=ctx.locale,
        timezone=ctx.timezone,
        timezone_offset_minutes=ctx.tz_offset_minutes,
        timezone_offset_ms=ctx.tz_offset_minutes * 60 * 1000,
    )
    return out


def available() -> list[str]:
    return sorted(_COUNTRY_MAP.keys())