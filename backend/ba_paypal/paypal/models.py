from dataclasses import dataclass, field
import calendar
import random
import string
import time
import uuid
from typing import TypedDict


@dataclass
class UserInfo:
    first_name: str
    last_name: str
    email: str
    phone: str
    phone_local: str
    phone_country_code: str
    password: str
    dob: str  # DD/MM/YYYY
    cpf: str  # XXX.XXX.XXX-XX
    # 国家化扩展字段 (默认 None/空, 向后兼容)
    identity_document_type: str = ""
    identity_document_number: str = ""
    nationality: str = ""
    middle_name: str = ""
    kana_first: str = ""
    kana_last: str = ""
    crs_data: dict | None = None
    occupation: str = ""
    gender: str = ""
    place_of_birth: str = ""


@dataclass
class CardInfo:
    number: str
    expiry: str  # MM/YYYY
    cvv: str
    card_type: str = "CREDIT"
    issuer: str = "VISA"
    bin: str = ""


@dataclass
class BillingAddress:
    street: str
    house_number: str
    district: str
    city: str
    state: str
    postal_code: str
    country: str = "BR"


@dataclass
class SessionState:
    ba_token: str = ""
    ec_token: str = ""
    ssrt: str = ""
    ctx_id: str = ""
    nsid: str = ""
    d_id: str = ""
    user_id: str = ""
    datadome_cookie: str = ""
    datadome_clientid: str = ""
    tltsid: str = ""
    tltdid: str = ""
    tealeaf_serial_number: int = 0
    tealeaf_page_id: str = field(default_factory=lambda: f"P.{uuid.uuid4().hex[:24].upper()}")
    tealeaf_tab_id: str = field(default_factory=lambda: f"Y{random.randint(100, 999)}")
    tealeaf_start_time_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    paypal_client_metadata_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    euat_token: str = ""
    return_url: str = ""
    content_hash: str = ""
    content_identifier: str = ""
    content_manifest_url: str = ""
    content_manifest_key: str = ""
    signup_url: str = ""
    signup_context_ready: bool = False
    paypal_captcha_solved: bool = False
    show_create_account_action_id: str = ""
    create_user_action_id: str = ""
    submit_public_credential_action_id: str = ""
    fetch_device_fingerprint_action_id: str = ""
    modxo_country_action_id: str = ""
    modxo_country_action_bound: str = ""
    modxo_country_selected: bool = False
    modxo_pay_page_url: str = ""
    passkey_challenge: str = ""
    rp_id: str = ""
    login_phone_country_code: str = ""
    modxo_deployment_id: str = ""
    signup_fallback_reason: str = ""
    mtr_channel: str = ""
    mtr_client_metadata_id: str = ""
    mtr_api_key: str = ""
    mtr_is_qa: bool = False
    mtr_dfp_script_url: str = ""
    mtr_get_status: int = 0
    mtr_post_status: int = 0
    mtr_request_id: str = ""
    mtr_sealed_result: str = ""
    mtr_runtime_source: str = ""
    mtr_visitor_token: str = ""
    mtr_completed: bool = False
    mtr_completed_cmid: str = ""
    mtr_browser_result: dict[str, object] = field(default_factory=dict)
    captcha_synthetic_used: bool = False
    datadome_header_injected: bool = False
    fingerprint_source: str = ""
    roxy_browser: dict[str, object] = field(default_factory=dict)
    datadome_browser_solved: bool = False
    datadome_browser_result: dict[str, object] = field(default_factory=dict)
    risk_signals_runtime_source: str = ""
    risk_signals_browser_result: dict[str, object] = field(default_factory=dict)
    browser_profile: dict[str, object] = field(default_factory=dict)
    screen: dict[str, object] = field(default_factory=dict)
    viewport: dict[str, object] = field(default_factory=dict)
    device_fingerprint: dict[str, object] = field(default_factory=dict)
    pxp_guid: str = field(default_factory=lambda: uuid.uuid4().hex)
    page_start_time_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    fpti_calc: str = field(default_factory=lambda: uuid.uuid4().hex[:13])
    datadog_session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    datadog_view_ids: dict[str, object] = field(default_factory=dict)

    def update_from_cookies(self, cookies: dict[str, str]) -> None:
        if "nsid" in cookies:
            self.nsid = cookies["nsid"]
        if "d_id" in cookies:
            self.d_id = cookies["d_id"]
        if "datadome" in cookies:
            self.datadome_cookie = cookies["datadome"]
        if "TLTSID" in cookies:
            self.tltsid = cookies["TLTSID"]
        if "TLTDID" in cookies:
            self.tltdid = cookies["TLTDID"]
        euat_key = "AV894Kt2TSumQQrJwe-8mzmyREO"
        if euat_key in cookies:
            self.euat_token = cookies[euat_key]


def generate_random_email() -> str:
    first = random.choice(_BR_FIRST_NAMES).lower()
    last = random.choice(_BR_LAST_NAMES).lower()
    return _generate_br_email(first, last)


def generate_eteid() -> list[int | None]:
    return [
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        None,
        None,
    ]


# --- Random generators for Brazil ---


class _BrLocation(TypedDict):
    state: str
    city: str
    ceps: list[str]

_BR_FIRST_NAMES = [
    "Lucas", "Miguel", "Arthur", "Gabriel", "Pedro", "Matheus", "Rafael",
    "Bruno", "Felipe", "Gustavo", "Diego", "Caio", "Andre", "Thiago",
    "Leonardo", "Eduardo", "Henrique", "Vinicius", "Marcos", "Daniel",
    "Ana", "Maria", "Julia", "Laura", "Mariana", "Beatriz", "Camila",
    "Leticia", "Larissa", "Amanda", "Fernanda", "Carolina", "Isabela",
    "Renata", "Aline", "Patricia", "Bianca", "Bruna", "Clara", "Luana",
    "Sofia", "Helena", "Manuela", "Valentina", "Yasmin", "Alice", "Livia",
    "Lorena", "Vitoria", "Nina",
]

_BR_LAST_NAMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira",
    "Almeida", "Pereira", "Lima", "Gomes", "Costa", "Ribeiro", "Martins",
    "Carvalho", "Rocha", "Barbosa", "Melo", "Cardoso", "Teixeira", "Correia",
    "Moura", "Cunha", "Dias", "Nunes", "Moreira", "Vieira", "Monteiro",
    "Castro", "Araujo", "Campos", "Freitas", "Pinto", "Mendes", "Cavalcanti",
    "Nascimento", "Batista", "Andrade", "Reis", "Duarte", "Machado", "Farias",
    "Borges", "Miranda", "Fonseca", "Ramos", "Neves", "Tavares", "Peixoto",
    "Siqueira", "Moraes",
]

_BR_LOCATIONS: list[_BrLocation] = [
    {"state": "SP", "city": "Sao Paulo", "ceps": ["01001-000", "01310-100", "01415-001", "04094-050", "04543-011", "05010-000"]},
    {"state": "RJ", "city": "Rio de Janeiro", "ceps": ["20040-020", "22010-000", "22250-040", "22410-002", "22640-102", "23050-000"]},
    {"state": "MG", "city": "Belo Horizonte", "ceps": ["30130-010", "30140-071", "30310-009", "30421-169", "30640-070"]},
    {"state": "BA", "city": "Salvador", "ceps": ["40020-000", "40140-110", "40210-630", "41820-020", "41940-040"]},
    {"state": "PR", "city": "Curitiba", "ceps": ["80010-010", "80230-010", "80420-090", "80530-000", "81200-100"]},
    {"state": "RS", "city": "Porto Alegre", "ceps": ["90010-150", "90110-001", "90430-001", "90560-002", "91340-000"]},
    {"state": "PE", "city": "Recife", "ceps": ["50010-000", "51020-000", "52011-000", "52050-000", "51030-000"]},
    {"state": "CE", "city": "Fortaleza", "ceps": ["60025-060", "60160-230", "60325-000", "60410-440", "60811-341"]},
    {"state": "DF", "city": "Brasilia", "ceps": ["70040-010", "70297-400", "70390-025", "70770-522", "71919-540"]},
    {"state": "SC", "city": "Florianopolis", "ceps": ["88010-400", "88015-201", "88020-300", "88034-000", "88062-000"]},
    {"state": "GO", "city": "Goiania", "ceps": ["74003-010", "74110-010", "74210-010", "74605-010", "74810-100"]},
    {"state": "PA", "city": "Belem", "ceps": ["66010-000", "66015-160", "66035-170", "66050-000", "66110-000"]},
    {"state": "AM", "city": "Manaus", "ceps": ["69005-040", "69010-000", "69020-010", "69050-001", "69058-795"]},
    {"state": "ES", "city": "Vitoria", "ceps": ["29010-120", "29015-120", "29050-335", "29055-450", "29060-270"]},
    {"state": "MT", "city": "Cuiaba", "ceps": ["78005-370", "78010-000", "78020-400", "78048-000", "78060-900"]},
    {"state": "MS", "city": "Campo Grande", "ceps": ["79002-071", "79004-000", "79010-040", "79020-210", "79040-450"]},
    {"state": "RN", "city": "Natal", "ceps": ["59010-000", "59020-100", "59030-200", "59064-100", "59090-000"]},
    {"state": "PB", "city": "Joao Pessoa", "ceps": ["58010-000", "58013-000", "58030-001", "58045-010", "58051-900"]},
    {"state": "AL", "city": "Maceio", "ceps": ["57020-000", "57035-000", "57036-000", "57046-000", "57055-000"]},
    {"state": "SE", "city": "Aracaju", "ceps": ["49010-000", "49015-000", "49020-000", "49035-000", "49050-000"]},
    {"state": "SP", "city": "Campinas", "ceps": ["13010-001", "13015-000", "13020-060", "13024-200", "13083-970", "13100-000"]},
    {"state": "SP", "city": "Santos", "ceps": ["11010-150", "11013-001", "11015-200", "11025-001", "11045-400", "11060-001"]},
    {"state": "RJ", "city": "Niteroi", "ceps": ["24020-125", "24030-060", "24210-200", "24220-900", "24340-005", "24350-010"]},
    {"state": "MG", "city": "Uberlandia", "ceps": ["38400-100", "38400-170", "38405-202", "38408-100", "38411-186", "38414-064"]},
    {"state": "BA", "city": "Feira de Santana", "ceps": ["44001-000", "44002-000", "44020-000", "44050-000", "44075-000", "44088-000"]},
    {"state": "PR", "city": "Londrina", "ceps": ["86010-000", "86015-000", "86020-000", "86026-010", "86039-000", "86050-000"]},
    {"state": "RS", "city": "Caxias do Sul", "ceps": ["95010-000", "95020-000", "95032-000", "95040-000", "95052-000", "95070-560"]},
    {"state": "PE", "city": "Olinda", "ceps": ["53010-000", "53020-000", "53120-000", "53130-000", "53240-000", "53330-000"]},
    {"state": "CE", "city": "Juazeiro do Norte", "ceps": ["63010-000", "63020-000", "63030-000", "63040-000", "63050-000", "63060-000"]},
    {"state": "GO", "city": "Anapolis", "ceps": ["75020-010", "75023-040", "75024-030", "75043-010", "75110-390", "75113-570"]},
    {"state": "PA", "city": "Santarem", "ceps": ["68005-000", "68010-000", "68015-000", "68020-000", "68035-000", "68040-000"]},
    {"state": "SC", "city": "Joinville", "ceps": ["89201-000", "89202-000", "89203-000", "89204-000", "89218-000", "89221-000"]},
]

_BR_STATE_NAMES = {
    "SP": "São Paulo", "RJ": "Rio de Janeiro", "MG": "Minas Gerais",
    "BA": "Bahia", "PR": "Paraná", "RS": "Rio Grande do Sul",
    "PE": "Pernambuco", "CE": "Ceará", "DF": "Distrito Federal",
    "SC": "Santa Catarina", "GO": "Goiás", "PA": "Pará", "AM": "Amazonas",
    "ES": "Espírito Santo", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "RN": "Rio Grande do Norte", "PB": "Paraíba", "AL": "Alagoas",
    "SE": "Sergipe",
}

_BR_STREET_NAMES = [
    "Avenida Paulista", "Rua Augusta", "Rua Oscar Freire", "Rua Vergueiro",
    "Rua Haddock Lobo", "Avenida Atlantica", "Rua Voluntarios da Patria",
    "Rua Visconde de Piraja", "Rua das Laranjeiras", "Avenida Afonso Pena",
    "Rua da Bahia", "Rua Paraiba", "Avenida do Contorno", "Rua Curitiba",
    "Avenida Sete de Setembro", "Rua Chile", "Rua das Hortensias",
    "Avenida Tancredo Neves", "Rua XV de Novembro", "Avenida Batel",
    "Rua Marechal Deodoro", "Rua Comendador Araujo", "Avenida Ipiranga",
    "Rua dos Andradas", "Rua Padre Chagas", "Avenida Borges de Medeiros",
    "Rua da Aurora", "Avenida Boa Viagem", "Rua do Hospicio", "Rua Benfica",
    "Avenida Beira Mar", "Rua Barão de Aracati", "Rua Costa Barros",
    "Avenida Dom Luis", "SQS 308 Bloco A", "CLN 102 Bloco B",
    "SHIS QI 05 Conjunto 02", "Avenida das Nacoes", "Rua Bocaiuva",
    "Rua Felipe Schmidt", "Avenida Mauro Ramos", "Rua Esteves Junior",
    "Avenida Goias", "Rua 10", "Avenida T-63", "Rua 9", "Avenida Nazare",
    "Travessa Padre Eutiquio", "Rua dos Mundurucus", "Avenida Almirante Barroso",
]

_BR_DISTRICTS = [
    "Centro", "Bela Vista", "Jardim America", "Vila Mariana", "Pinheiros",
    "Consolacao", "Liberdade", "Santa Cecilia", "Moema", "Itaim Bibi",
    "Perdizes", "Savassi", "Boa Viagem", "Batel", "Moinhos de Vento",
]

_BR_STREETS_BY_CITY = {
    "Sao Paulo": ["Avenida Paulista", "Rua Augusta", "Rua Oscar Freire", "Rua Vergueiro", "Rua Haddock Lobo"],
    "Rio de Janeiro": ["Avenida Atlantica", "Rua Voluntarios da Patria", "Rua Visconde de Piraja", "Rua das Laranjeiras"],
    "Belo Horizonte": ["Avenida Afonso Pena", "Rua da Bahia", "Rua Paraiba", "Avenida do Contorno", "Rua Curitiba"],
    "Salvador": ["Avenida Sete de Setembro", "Rua Chile", "Rua das Hortensias", "Avenida Tancredo Neves"],
    "Curitiba": ["Rua XV de Novembro", "Avenida Batel", "Rua Marechal Deodoro", "Rua Comendador Araujo"],
    "Porto Alegre": ["Avenida Ipiranga", "Rua dos Andradas", "Rua Padre Chagas", "Avenida Borges de Medeiros"],
    "Recife": ["Rua da Aurora", "Avenida Boa Viagem", "Rua do Hospicio", "Rua Benfica"],
    "Fortaleza": ["Avenida Beira Mar", "Rua Barão de Aracati", "Rua Costa Barros", "Avenida Dom Luis"],
    "Brasilia": ["SQS 308 Bloco A", "CLN 102 Bloco B", "SHIS QI 05 Conjunto 02", "Avenida das Nacoes"],
    "Florianopolis": ["Rua Bocaiuva", "Rua Felipe Schmidt", "Avenida Mauro Ramos", "Rua Esteves Junior"],
    "Goiania": ["Avenida Goias", "Rua 10", "Avenida T-63", "Rua 9"],
    "Belem": ["Avenida Nazare", "Travessa Padre Eutiquio", "Rua dos Mundurucus", "Avenida Almirante Barroso"],
    "Manaus": ["Avenida Eduardo Ribeiro", "Rua Miranda Leao", "Avenida Djalma Batista", "Rua Ramos Ferreira"],
    "Vitoria": ["Avenida Jeronimo Monteiro", "Rua Sete de Setembro", "Avenida Nossa Senhora da Penha", "Rua Aleixo Netto"],
    "Cuiaba": ["Avenida Getulio Vargas", "Rua Barão de Melgaço", "Avenida Historiador Rubens de Mendonça", "Rua 24 de Outubro"],
    "Campo Grande": ["Avenida Afonso Pena", "Rua 14 de Julho", "Rua Dom Aquino", "Avenida Mato Grosso"],
    "Natal": ["Avenida Prudente de Morais", "Rua Mossoro", "Avenida Hermes da Fonseca", "Rua Potengi"],
    "Joao Pessoa": ["Avenida Epitacio Pessoa", "Rua Duque de Caxias", "Avenida Almirante Tamandare", "Rua das Trincheiras"],
    "Maceio": ["Avenida Fernandes Lima", "Rua do Comercio", "Avenida Doutor Antonio Gouveia", "Rua Barao de Maceio"],
    "Aracaju": ["Avenida Beira Mar", "Rua Itabaiana", "Avenida Ivo do Prado", "Rua Laranjeiras"],
    "Campinas": ["Avenida Francisco Glicerio", "Rua Conceicao", "Avenida Orosimbo Maia", "Rua Barreto Leme", "Avenida Jose de Souza Campos"],
    "Santos": ["Avenida Conselheiro Nebias", "Avenida Ana Costa", "Rua XV de Novembro", "Avenida Washington Luis", "Rua Tolentino Filgueiras"],
    "Niteroi": ["Rua da Conceicao", "Avenida Amaral Peixoto", "Rua Gavio Peixoto", "Avenida Roberto Silveira", "Rua Miguel de Frias"],
    "Uberlandia": ["Avenida Afonso Pena", "Rua Olegario Maciel", "Avenida Joao Naves de Avila", "Rua Duque de Caxias", "Avenida Rondon Pacheco"],
    "Feira de Santana": ["Avenida Getulio Vargas", "Rua Conselheiro Franco", "Avenida Senhor dos Passos", "Rua Marechal Deodoro", "Avenida Maria Quiteria"],
    "Londrina": ["Avenida Higienopolis", "Rua Sergipe", "Avenida Juscelino Kubitschek", "Rua Pio XII", "Avenida Madre Leonia Milito"],
    "Caxias do Sul": ["Avenida Julio de Castilhos", "Rua Sinimbu", "Rua Feijo Junior", "Avenida Rio Branco", "Rua Os Dezoito do Forte"],
    "Olinda": ["Avenida Presidente Kennedy", "Rua do Sol", "Avenida Getulio Vargas", "Rua Prudente de Morais", "Avenida Carlos de Lima Cavalcanti"],
    "Juazeiro do Norte": ["Rua Sao Pedro", "Avenida Padre Cicero", "Rua Santa Luzia", "Avenida Castelo Branco", "Rua Sao Francisco"],
    "Anapolis": ["Avenida Brasil", "Rua Engenheiro Portela", "Avenida Goias", "Rua Manoel DAbadia", "Avenida Universitaria"],
    "Santarem": ["Avenida Rui Barbosa", "Travessa dos Martires", "Avenida Mendonca Furtado", "Rua Galdino Veloso", "Avenida Borges Leal"],
    "Joinville": ["Rua XV de Novembro", "Rua Blumenau", "Avenida Getulio Vargas", "Rua do Principe", "Rua Otto Boehm"],
}

_BR_CARD_BINS = [
    ("414709", 16, "VISA"),
    ("516292", 16, "MASTER_CARD"),
]

_BR_EMAIL_DOMAINS = [
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com.br",
    "icloud.com", "uol.com.br", "bol.com.br",
]


def _luhn_checksum(partial: str) -> int:
    """Calculate the Luhn check digit for a partial card number (without the check digit)."""
    total = 0
    alternate = True
    for ch in reversed(partial):
        digit = int(ch)
        if alternate:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        alternate = not alternate
    return 0 if total % 10 == 0 else 10 - (total % 10)


def _generate_br_email(first_name: str, last_name: str) -> str:
    return (
        f"{first_name.lower()}.{last_name.lower()}"
        f"{random.randint(10, 9999)}@{random.choice(_BR_EMAIL_DOMAINS)}"
    )


def generate_card(
    proxy_url: str | None = None,
    country: str = "BR",
    product_class_hint: str | None = None,
    used_bins: set | None = None,
) -> CardInfo:
    """国家化卡生成: 按国家 BIN 池选号 (未收录回退 US), Luhn 校验位, CVV 长度随 issuer。

    旧签名 `generate_card(proxy_url=...)` 保留 (默认 country="BR")。
    """
    del proxy_url
    if used_bins is None:
        used_bins = None
    from paypal.identity_lib import generate_country_card, issuer_type_for

    card = generate_country_card(country, used_bins)
    card_type = card["product_class"]
    if product_class_hint in {"CREDIT", "DEBIT"}:
        card_type = product_class_hint
    return CardInfo(
        number=card["number"],
        expiry=card["expiry"],
        cvv=card["cvv"],
        card_type=card_type,
        issuer=issuer_type_for(card["number"]),
        bin=card["bin"],
    )


def generate_cpf() -> str:
    while True:
        digits = [random.randint(0, 9) for _ in range(9)]
        if not all(digit == digits[0] for digit in digits):
            break

    first_sum = sum(digit * (10 - idx) for idx, digit in enumerate(digits))
    first = 0 if first_sum % 11 < 2 else 11 - (first_sum % 11)
    second_base = [*digits, first]
    second_sum = sum(digit * (11 - idx) for idx, digit in enumerate(second_base))
    second = 0 if second_sum % 11 < 2 else 11 - (second_sum % 11)
    cpf = "".join(str(digit) for digit in [*digits, first, second])
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def generate_dob() -> str:
    year = random.randint(1970, 2000)
    month = random.randint(1, 12)
    days_in_month = calendar.monthrange(year, month)[1]
    day = random.randint(1, days_in_month)
    return f"{day:02d}/{month:02d}/{year}"


def generate_password() -> str:
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    required = "0123456789!@#$%&*"
    chars = lower + upper + required
    length = random.randint(8, 20)
    pwd = [random.choice(required)]
    while len(pwd) < length:
        pwd.append(random.choice(chars))
    random.shuffle(pwd)
    return "".join(pwd)


def generate_user(phone: str = "", country: str = "BR") -> UserInfo:
    """国家化身份生成。

    country != BR: 委托 identity_lib (姓名/邮箱/生日/证件/地址/电话全国家化)。
    country == BR: 保留原巴西路径 (向后兼容)。
    """
    cc = (country or "BR").strip().upper()
    if cc != "BR":
        from paypal.identity_lib import get_country_profile

        ident = get_country_profile(cc)
        phone_full = _normalize_phone_full(phone or ident.phone_number or "", ident.phone_country)
        phone_local, phone_cc = _split_phone(phone_full, ident.phone_country)
        return UserInfo(
            first_name=ident.first_name,
            last_name=ident.last_name,
            email=ident.email,
            phone=phone_full,
            phone_local=phone_local,
            phone_country_code=phone_cc,
            password=ident.password,
            dob=ident.dob,
            cpf=ident.identity_document_number or "",
            identity_document_type=ident.identity_document_type,
            identity_document_number=ident.identity_document_number,
            nationality=ident.nationality,
            middle_name=ident.middle_name,
            kana_first=ident.kana_first,
            kana_last=ident.kana_last,
        )

    phone_full = _normalize_phone_full(phone, "+55")
    phone_local, phone_cc = _split_phone(phone_full, "+55")
    first = random.choice(_BR_FIRST_NAMES)
    last = random.choice(_BR_LAST_NAMES)
    return UserInfo(
        first_name=first,
        last_name=last,
        email=generate_random_email(),
        phone=phone_full,
        phone_local=phone_local,
        phone_country_code=phone_cc,
        password=generate_password(),
        dob=generate_dob(),
        cpf=generate_cpf(),
        nationality="BR",
    )


def _normalize_phone_full(phone: str, default_cc: str) -> str:
    """把任意格式手机号归一到 +CC 开头完整号码。

    空值: 生成合成本国号码 (接码激活后会由 _update_user_phone 替换, 不落真号);
    显式非法值: 抛错。
    """
    phone = str(phone or "").strip()
    if not phone:
        return _synthetic_phone(default_cc)
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid phone: {phone!r}")
    if phone.startswith("+"):
        return f"+{digits}"
    return f"{default_cc}{digits}"


def _synthetic_phone(cc_prefix: str) -> str:
    """合成一个"本地结构真实但非真实号码"的占位号 (后续被接码号覆盖)。"""
    try:
        from paypal.identity_lib import _generate_national_phone
        local = _generate_national_phone(str(cc_prefix).lstrip("+"))
    except Exception:
        local = "9" + "".join(str(random.randint(0, 9)) for _ in range(9))
    return f"+{str(cc_prefix).lstrip('+')}{local}"


def _split_phone(phone_full: str, default_cc: str) -> tuple[str, str]:
    digits = "".join(ch for ch in phone_full if ch.isdigit())
    cc = str(default_cc or "+1").lstrip("+")
    local = digits
    if digits.startswith(cc) and len(digits) > len(cc) + 2:
        local = digits[len(cc):]
    return local, f"+{cc}"


def generate_address(country: str = "BR") -> BillingAddress:
    """国家化账单地址。country != BR 委托 identity_lib 地址池 (line2 语义按国)。"""
    cc = (country or "BR").strip().upper()
    if cc != "BR":
        from paypal.identity_lib import generate_country_address

        addr = generate_country_address(cc)
        line1 = addr.get("line1") or ""
        line2 = addr.get("line2") or ""
        # line1 内嵌门牌号 (如 "Av Paulista 1000") 时分出行号字段
        house_number = ""
        street = line1
        parts = line1.rsplit(" ", 1)
        house_number = str(addr.get("house_number") or "")
        if len(parts) == 2 and parts[1].isdigit():
            street, house_number = parts
        return BillingAddress(
            street=street,
            house_number=house_number,
            district=line2,
            city=addr.get("city") or "",
            state=addr.get("state") or "",
            postal_code=addr.get("postal_code") or "",
            country=cc,
        )

    location = random.choice(_BR_LOCATIONS)
    state = location["state"]
    city = location["city"]
    postal_code = random.choice(location["ceps"])
    street = random.choice(_BR_STREETS_BY_CITY.get(city, _BR_STREET_NAMES))
    house_number = str(random.randint(12, 4899))

    return BillingAddress(
        street=street,
        house_number=house_number,
        district=random.choice(_BR_DISTRICTS),
        city=city,
        state=state,
        postal_code=postal_code,
        country="BR",
    )
