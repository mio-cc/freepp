#!/usr/bin/env python3
"""按国家批量生成 PayPal 注册身份数据 (身份字段算法全部真实校验位)。

用法:
    python _gen_country_identities.py US
    python _gen_country_identities.py ALL
    python _gen_country_identities.py US 10        # 10 份, 默认 1
    python _gen_country_identities.py US 5 --json out_us.json
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from ba_paypal.paypal import identity_lib as lib  # noqa: E402


def self_check(country: str):
    """生成一次并内部校验 (供 smoke test)。"""
    ident = lib.get_country_profile(country)
    doc = ident.identity_document_number
    if not doc:
        return ident
    if country == "TH":
        assert lib._mod11_check_digit_v2(doc[:-1], list(range(13, 1, -1))) == int(doc[-1])
    elif country == "AE":
        assert lib._verify_luhn(doc) and doc.startswith("784")
    elif country == "KR" and ident.identity_document_type == "NATIONAL_ID":
        assert lib._mod11_check_digit_kr(doc[:-1]) == int(doc[-1])
    elif country == "BR":
        d = [c for c in doc if c.isdigit()]
        ok = True
        for t in (9, 10):
            s = sum(int(d[i]) * (t + 1 - i) for i in range(t))
            c = 0 if s % 11 < 2 else 11 - s % 11
            ok = ok and int(d[t]) == c
        assert ok
    elif country == "ZA":
        assert lib._verify_luhn(doc)
    elif country == "AR":
        assert lib._mod11_check_digit_ar(doc[:-1]) == int(doc[-1])
    elif country == "VN":
        assert len(doc) == 12 and doc.isdigit()
    elif country == "BH":
        assert len(doc) == 9 and doc.isdigit()
    elif country == "MX":
        total = sum(lib._curp_value(c) * (18 - i) for i, c in enumerate(doc[:-1]))
        assert int(doc[-1]) == (10 - total % 10) % 10
    return ident


def main():
    parser = argparse.ArgumentParser(description="按国家生成 PayPal 身份数据")
    parser.add_argument("country", help="国家码 (ISO2) 或 ALL")
    parser.add_argument("count", nargs="?", type=int, default=1, help="生成份数")
    parser.add_argument("--json", default="", help="输出 JSON 文件")
    parser.add_argument("--no-verify", action="store_true", help="跳过校验位自检")
    args = parser.parse_args()

    random.seed()
    countries = sorted(lib.available_countries()) if args.country.upper() == "ALL" else [args.country.upper()]
    out = {}
    for cc in countries:
        try:
            if args.country.upper() != "ALL":
                profile = lib.profile_summary(cc)
                print(f"[{cc}] fields={','.join(profile['fields']) or '(none)'} "
                      f"id_types={','.join(profile['id_types']) or '(none)'}")
            items = []
            for _ in range(max(1, args.count)):
                ident = lib.get_country_profile(cc) if args.no_verify else self_check(cc)
                items.append(ident.to_dict())
                doc = f"{ident.identity_document_type or '-'}:{ident.identity_document_number or '-'}"
                kana = f" kana={ident.kana_first} {ident.kana_last}" if ident.kana_first else ""
                print(f"  [{cc}] {ident.first_name} {ident.last_name} | {ident.dob} | {doc}{kana}")
            out[cc] = items[0] if args.count <= 1 else items
        except Exception as exc:
            print(f"[{cc}] ERROR: {exc}", file=sys.stderr)

    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written: {args.json}")


if __name__ == "__main__":
    main()