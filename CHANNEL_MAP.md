# 渠道 × 国家 对应表 (OpenAI 出口实测)

> 数据: 195 国全量扫描 + 68 国复查 + 深挖/币种回退终扫 (2026-08)。
> 全部为 checkout=200 且 init 拿到 payment_method_types 的记录。

## 总览

- 可下单国家 (co=200 且拿到渠道): **178** / 195
- 403 硬封锁: HK, MO, VE
- 400 永久失败: GI
- 出口无IP (711 geo skip): AS, BL, CF, ER, EU, FO, GL, GM, IQ, NA, OM, PW, WS

## 渠道分布

- **card** (165国): AD, AE, AG, AI, AL, AM, AO, AR, AT, AW, AZ, BA, BB, BD, BE, BF, BG, BH, BI, BJ, BM, BN, BO, BS, BT, BW, BZ, CD, CG, CH, CI, CK, CL, CM, CO, CR, CV, CW, CY, CZ, DJ, DK, DM, DO, DZ, EC, EE, EG, ES, ET, FI, FJ, GA, GD, GE, GH, GN, GQ, GR, GT, GW, GY, HN, HR, HT, ID, IE, IL, IN, IS, IT, JM, JO, KE, KG, KH, KI, KM, KN, KR, KW, KY, KZ, LA, LB, LC, LI, LK, LR, LS, LT, LU, LV, MA, MC, ME, MG, ML, MN, MQ, MR, MS, MT, MU, MV, MW, MX, MY, MZ, NC, NE, NG, NI, NO, NP, NZ, PA, PE, PG, PK, PL, PM, PR, PY, QA, RO, RS, RW, SA, SB, SC, SE, SI, SK, SL, SM, SN, SR, ST, SV, SX, SZ, TC, TD, TG, TH, TJ, TL, TM, TN, TO, TT, TW, TZ, UG, UY, UZ, VG, VI, VN, VU, YT, ZA, ZM, ZW
- **paypal** (143国): AD, AG, AI, AL, AM, AO, AR, AT, AW, AZ, BA, BB, BD, BE, BF, BG, BH, BI, BJ, BM, BN, BO, BS, BT, BW, BZ, CD, CG, CH, CI, CK, CM, CR, CV, CW, CY, CZ, DJ, DK, DM, DO, DZ, EC, EE, ES, ET, FI, FJ, GA, GD, GE, GH, GN, GQ, GR, GT, GW, GY, HN, HR, HT, IE, IS, IT, JM, JO, KE, KG, KH, KI, KM, KN, KW, KY, LA, LB, LC, LI, LK, LR, LS, LT, LV, MA, MC, ME, MG, ML, MN, MQ, MR, MS, MT, MU, MV, MW, MZ, NC, NE, NI, NO, NP, NZ, PA, PG, PL, PM, PR, PY, RO, RS, RW, SB, SC, SE, SI, SK, SL, SM, SN, SR, ST, SV, SX, SZ, TC, TD, TG, TJ, TL, TM, TN, TO, TT, UG, UY, UZ, VG, VI, VU, YT, ZM, ZW
- **(init-na)** (30国): AS, BL, CF, ER, EU, FO, GF, GI, GL, GM, GP, GU, HK, HU, IQ, MD, MK, MO, NA, OM, PF, PH, PT, PW, RE, TR, UA, VC, VE, WS
- **bizum** (1国): ES
- **gopay** (1国): ID
- **upi** (1国): IN
- **kakao_pay** (1国): KR
- **naver_pay** (1国): KR

## 国家明细

| 国家 | 状态 | 渠道 |
|---|---|---|
| AD | 200 | card, paypal |
| AE | 200 | card |
| AG | 200 | card, paypal |
| AI | 200 | card, paypal |
| AL | 200 | card, paypal |
| AM | 200 | card, paypal |
| AO | 200 | card, paypal |
| AR | 200 | card, paypal |
| AS | geo skip | - |
| AT | 200 | card, paypal |
| AW | 200 | card, paypal |
| AZ | 200 | card, paypal |
| BA | 200 | card, paypal |
| BB | 200 | card, paypal |
| BD | 200 | card, paypal |
| BE | 200 | card, paypal |
| BF | 200 | card, paypal |
| BG | 200 | card, paypal |
| BH | 200 | card, paypal |
| BI | 200 | card, paypal |
| BJ | 200 | card, paypal |
| BL | geo skip | - |
| BM | 200 | card, paypal |
| BN | 200 | card, paypal |
| BO | 200 | card, paypal |
| BS | 200 | card, paypal |
| BT | 200 | card, paypal |
| BW | 200 | card, paypal |
| BZ | 200 | card, paypal |
| CD | 200 | card, paypal |
| CF | geo skip | - |
| CG | 200 | card, paypal |
| CH | 200 | card, paypal |
| CI | 200 | card, paypal |
| CK | 200 | card, paypal |
| CL | 200 | card |
| CM | 200 | card, paypal |
| CO | 200 | card |
| CR | 200 | card, paypal |
| CV | 200 | card, paypal |
| CW | 200 | card, paypal |
| CY | 200 | card, paypal |
| CZ | 200 | card, paypal |
| DJ | 200 | card, paypal |
| DK | 200 | card, paypal |
| DM | 200 | card, paypal |
| DO | 200 | card, paypal |
| DZ | 200 | card, paypal |
| EC | 200 | card, paypal |
| EE | 200 | card, paypal |
| EG | 200 | card |
| ER | geo skip | - |
| ES | 200 | card, paypal, bizum |
| ET | 200 | card, paypal |
| EU | geo skip | - |
| FI | 200 | card, paypal |
| FJ | 200 | card, paypal |
| FO | geo skip | - |
| GA | 200 | card, paypal |
| GD | 200 | card, paypal |
| GE | 200 | card, paypal |
| GF | 200 | (init-na) |
| GH | 200 | card, paypal |
| GI | 400 dead | - |
| GL | geo skip | - |
| GM | geo skip | - |
| GN | 200 | card, paypal |
| GP | 200 | (init-na) |
| GQ | 200 | card, paypal |
| GR | 200 | card, paypal |
| GT | 200 | card, paypal |
| GU | 200 | (init-na) |
| GW | 200 | card, paypal |
| GY | 200 | card, paypal |
| HK | 403 blocked | - |
| HN | 200 | card, paypal |
| HR | 200 | card, paypal |
| HT | 200 | card, paypal |
| HU | 200 | (init-na) |
| ID | 200 | card, gopay |
| IE | 200 | card, paypal |
| IL | 200 | card |
| IN | 200 | card, upi |
| IQ | geo skip | - |
| IS | 200 | card, paypal |
| IT | 200 | card, paypal |
| JM | 200 | card, paypal |
| JO | 200 | card, paypal |
| KE | 200 | card, paypal |
| KG | 200 | card, paypal |
| KH | 200 | card, paypal |
| KI | 200 | card, paypal |
| KM | 200 | card, paypal |
| KN | 200 | card, paypal |
| KR | 200 | card, kakao_pay, naver_pay |
| KW | 200 | card, paypal |
| KY | 200 | card, paypal |
| KZ | 200 | card |
| LA | 200 | card, paypal |
| LB | 200 | card, paypal |
| LC | 200 | card, paypal |
| LI | 200 | card, paypal |
| LK | 200 | card, paypal |
| LR | 200 | card, paypal |
| LS | 200 | card, paypal |
| LT | 200 | card, paypal |
| LU | 200 | card |
| LV | 200 | card, paypal |
| MA | 200 | card, paypal |
| MC | 200 | card, paypal |
| MD | 200 | (init-na) |
| ME | 200 | card, paypal |
| MG | 200 | card, paypal |
| MK | 200 | (init-na) |
| ML | 200 | card, paypal |
| MN | 200 | card, paypal |
| MO | 403 blocked | - |
| MQ | 200 | card, paypal |
| MR | 200 | card, paypal |
| MS | 200 | card, paypal |
| MT | 200 | card, paypal |
| MU | 200 | card, paypal |
| MV | 200 | card, paypal |
| MW | 200 | card, paypal |
| MX | 200 | card |
| MY | 200 | card |
| MZ | 200 | card, paypal |
| NA | geo skip | - |
| NC | 200 | card, paypal |
| NE | 200 | card, paypal |
| NG | 200 | card |
| NI | 200 | card, paypal |
| NO | 200 | card, paypal |
| NP | 200 | card, paypal |
| NZ | 200 | card, paypal |
| OM | geo skip | - |
| PA | 200 | card, paypal |
| PE | 200 | card |
| PF | 200 | (init-na) |
| PG | 200 | card, paypal |
| PH | 200 | (init-na) |
| PK | 200 | card |
| PL | 200 | card, paypal |
| PM | 200 | card, paypal |
| PR | 200 | card, paypal |
| PT | 200 | (init-na) |
| PW | geo skip | - |
| PY | 200 | card, paypal |
| QA | 200 | card |
| RE | 200 | (init-na) |
| RO | 200 | card, paypal |
| RS | 200 | card, paypal |
| RW | 200 | card, paypal |
| SA | 200 | card |
| SB | 200 | card, paypal |
| SC | 200 | card, paypal |
| SE | 200 | card, paypal |
| SI | 200 | card, paypal |
| SK | 200 | card, paypal |
| SL | 200 | card, paypal |
| SM | 200 | card, paypal |
| SN | 200 | card, paypal |
| SR | 200 | card, paypal |
| ST | 200 | card, paypal |
| SV | 200 | card, paypal |
| SX | 200 | card, paypal |
| SZ | 200 | card, paypal |
| TC | 200 | card, paypal |
| TD | 200 | card, paypal |
| TG | 200 | card, paypal |
| TH | 200 | card |
| TJ | 200 | card, paypal |
| TL | 200 | card, paypal |
| TM | 200 | card, paypal |
| TN | 200 | card, paypal |
| TO | 200 | card, paypal |
| TR | 200 | (init-na) |
| TT | 200 | card, paypal |
| TW | 200 | card |
| TZ | 200 | card |
| UA | 200 | (init-na) |
| UG | 200 | card, paypal |
| UY | 200 | card, paypal |
| UZ | 200 | card, paypal |
| VC | 200 | (init-na) |
| VE | 403 blocked | - |
| VG | 200 | card, paypal |
| VI | 200 | card, paypal |
| VN | 200 | card |
| VU | 200 | card, paypal |
| WS | geo skip | - |
| YT | 200 | card, paypal |
| ZA | 200 | card |
| ZM | 200 | card, paypal |
| ZW | 200 | card, paypal |