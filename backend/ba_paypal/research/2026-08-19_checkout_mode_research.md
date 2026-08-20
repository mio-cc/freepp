# Checkout 模式研究档案 (2026-08-19)

## 背景
研究 GB 区 PayPal Plus $0 checkout 时, 对 checkout 的 `ui_mode`(hosted/custom) × `promo_inline`(内联/不内联) 四种组合做对照实验, 发现了之前认知的错误并修正。

## 核心发现

### 1. update 段是压 0 的执行者, 不是 checkout 内联

**之前错误认知**: "custom 内联 promo 在 checkout 阶段就压 0, hosted update 注入 promo 被忽略"

**实际(对照实验修正)**:
- A组 (custom+内联, 跳过update): init#1 = 1667 全价, discount=None → **checkout 内联被服务端忽略**
- B组 (custom+内联, 经update): init#1 = 1667 → update → init#2 = **0** → **update 才是压 0 执行者**
- D组 (hosted+内联, 经update): init#1 = **0** → update → init#2 = **0** → **hosted+内联 checkout 直接压 0**

### 2. hosted + 内联 promo 是最强组合 (D组)

| 组 | ui_mode | 内联promo | 下发会话 | init#1 | update | init#2 |
|----|---------|-----------|----------|--------|--------|--------|
| A  | custom  | yes       | cs_live_ | 1667   | 跳过   | —      |
| B  | custom  | yes       | cs_live_ | 1667   | 经     | **0**  |
| C  | hosted  | no        | oaics_   | None   | 经     | None   |
| D  | hosted  | yes       | cs_live_ | **0**  | 经     | **0**  |

D组 (hosted+内联) checkout 阶段就直接压 0, update 段幂等确认保持 0。

### 3. update 对已 0 状态是幂等的

- checkout 已压 0 (如D组 init#1=0): update 后 init#2 仍 = 0 (no-op/确认)
- checkout 未压 0 (如B组 init#1=1667): update 把 1667 压成 0 (执行者)
- **update 不会破坏已 0 状态**, 是安全且必要的兜底

### 4. 会话类型 (oaics_ / cs_live_) 由服务端下发, 不可控

多次实验: 同一 checkout 参数组合 (custom+内联+sentinel) 反复出 cs_live_, 而 hosted 不内联偶尔出 oaics_。
**会话类型是 ChatGPT 服务端按自身逻辑决定的**, 与我们的 checkout 参数 (ui_mode/promo_inline/sentinel) 没有稳定因果关系。
C组那次 oaics_ 纯粹是服务端那次的分发改决定。

### 5. approve = blocked 是最终未解 blocker

无论 cust 还是 host, confirm 后 approve 返回 `{"result":"blocked"}`。
ChatGPT 风控对 datacenter IP + 新账号的 $0 checkout 拒绝 approve。

## 结论与产品化

基于以上发现, 新增 `checkout_mode` 配置字段, 允许手动选择 4 种组合:
- `auto`(默认): 保持���项目逻辑 (paypal: 未探测先 custom+内联, cs_live 走 hosted 七段; oaics_ 自动分流走 oaics 五段)
- `host_inline`: hosted + 内联 promo (D组, 最强, checkout 直接压 0)
- `host_no_inline`: hosted + 不内联 (原 update 压 0 模式)
- `cust_inline`: custom + 内联 promo (B组, 靠 update 补救压 0)
- `cust_no_inline`: custom + 不内联

**oaics 自动分流逻辑不变**: 服务端下发 oaics_ 会话时仍走 oaics 五段, checkout_mode 仅影响 cs_live_ 七段路径的 checkout 参数。
