/* ==========================================================================
   全局类型定义
   ========================================================================== */

/** 7 段链路顺序 */
export const STAGE_ORDER = [
  "checkout", "init", "update", "provider", "approve", "poll", "resolve"
] as const;
export type StageName = typeof STAGE_ORDER[number];

/** oaics custom Checkout 5 段链路顺序 (纯 HTTP, 无 init/update/approve/poll) */
export const OAICS_STAGE_ORDER = [
  "checkout", "taxes", "provider", "confirm", "resolve"
] as const;
export type OaicsStageName = typeof OAICS_STAGE_ORDER[number];

export const OAICS_STAGE_SHORT: Record<OaicsStageName, string> = {
  checkout: "CK", taxes: "TX",
  provider: "PM", confirm: "CF", resolve: "RS",
};

export const OAICS_STAGE_CN: Record<OaicsStageName, string> = {
  checkout: "结账", taxes: "账单提交",
  provider: "支付商", confirm: "确认", resolve: "解析",
};

export const STAGE_SHORT: Record<StageName, string> = {
  checkout: "CK", init: "IN", update: "UP",
  provider: "PM", approve: "AP", poll: "PL", resolve: "RS",
};

export const STAGE_CN: Record<StageName, string> = {
  checkout: "结账", init: "初始化", update: "更新",
  provider: "支付商", approve: "批准", poll: "轮询", resolve: "解析",
};

/** 段状态 */
export type StageState = "run" | "ok" | "fail";

export interface StageData {
  state: StageState;
  country: string;
  tryN: number;
  maxTry: number;
  /** 真实出口国家 (多源探测) */
  actualCountry?: string;
  exitIp?: string;
  geoConfidence?: number;
  /** 配置≠真实 => 飘移 */
  drifted?: boolean;
  /** 复用前段同国出口 (同 IP 不重复探测) 来源段 */
  reusedFrom?: string;
}

/** 链路状态 */
export type ChainStatus = "running" | "success" | "failed";

/** 链路模式: cs = 原七段 (hosted) / oaics = 五段 (custom 纯 HTTP) */
export type ChainLinkMode = "cs" | "oaics" | "";

/**
 * 【已废弃】S0 实时会话类型探测段 (2026-08-14 移除):
 * 原为提链开头用 checkout 段 IP 额外建单探测 oaics/cs_live, 现改由 S1 建单结果
 * 动态判定 (建出啥走啥), 前端链路监控列表"探"列已删除。类型保留仅供旧数据兼容。
 */
export type ProbeStageName = "probe";

export interface ChainState {
  stages: Partial<Record<StageName | OaicsStageName | ProbeStageName, StageData>>;
  status: ChainStatus;
  email: string;
  tokenSub: string;
  startTime: number;
  attempt: number;
  country: string;
  /** 链路模式 (node: cs/oaics, 由后端事件标记) */
  linkMode?: ChainLinkMode;
  /** 【已废弃】S0 探测到的会话类型 (探测段已移除, 该字段仅保留旧数据兼容) */
  detected?: string;
  /** 真实出口 (checkout 探测) */
  actualCountry?: string;
  exitIp?: string;
  geoConfidence?: number;
  /** 终端状态固化耗时 (秒): 成功后不再继续计时 */
  elapsed?: number;
  endTime?: number;
  url?: string;
  reason?: string;
  reasonText?: string;
  /** 渠道探测结果 (checkout 无 promo 后 init 的 payment_method_types 校验) */
  channelDetect?: {
    channel: string;
    methods: string[];
    present: boolean;
    country?: string;
  };
}

/** Token */
export interface Token {
  id: string;
  email: string;
  sub: string;
  account_id: string;
  plan_type: string;
  register_method: string;
  expires_at: string;
  status: string;
  created_at: string;
  last_run_at: string;
  /** 会话类型探测: cs_live / oaics / error:* / 空=未探测 */
  session_type?: string;
  /** 完整探测结果: {session_type, token, token_error, promo, paypal, amount} */
  probe?: Record<string, any>;
  /** 用户标签 */
  tags?: string[];
}

/** 代理节点 */
export interface ProxyNode {
  name: string;
  type: string;
  country_hint: string;
  port: number;
  latency: number;
  healthy: boolean | null;
  concurrent: number;
  max_concurrent: number;
  running: boolean;
}

/** 统计 */
export interface Stats {
  success: number;
  failure: number;
  byCountry: Record<string, number>;
  failByCountry: Record<string, number>;
  reasons: Record<string, number>;
  stageMatrix: Record<string, Record<string, { ok: number; fail: number }>>;
}

/** 样本记录 */
export interface Sample {
  ts: string;
  email: string;
  success: boolean;
  reason_code: string;
  reason_text: string;
  paypal_approve_url: string;
  amount_due: number;
  currency: string;
  country: string;
  stage_reached: string;
  chain_id: string;
  /** 真实出口地理 (多源探测) */
  actual_country?: string;
  requested_country?: string;
  exit_ip?: string;
  geo_confidence?: number;
}

/** 库存记录 */
export interface InventoryRecord {
  ba_id: string;
  email: string;
  country: string;
  paypal_url: string;
  amount: string | number;
  currency: string;
  time: string;
  /** 支付渠道 (提链分支产出) */
  channel?: string;
}

/** 日志条目 */
export interface LogEntry {
  ts: string;
  msg: string;
  level: "ok" | "info" | "warn" | "err";
  chainId: string;
}

/** WebSocket 事件 */
export interface WSEvent {
  type: string;
  [key: string]: any;
}

/* ==========================================================================
   PayPal BA 支付授权类型
   ========================================================================== */

/** BA 授权流程步骤 */
export const BA_STEPS = [
  "submit_email", "captcha", "sms", "signup", "consent_ba", "done",
] as const;
export type BAStep = typeof BA_STEPS[number];

export const BA_STEP_CN: Record<string, string> = {
  submit_email: "提交邮箱",
  captcha: "验证码",
  sms: "短信验证",
  signup: "注册会员",
  consent_ba: "同意授权",
  done: "完成",
  init_session: "初始化会话",
  authorize: "授权中",
  failed: "失败",
  FLOW_EXCEPTION: "流程异常",
  AUTHORIZE_EMPTY: "授权空结果",
  BUYER_NOT_SET: "未设买家",
};
export interface BAAuthRecord {
  ba_token: string;
  email: string;
  approve_url: string;
  status: "pending" | "running" | "success" | "failed";
  step: BAStep;
  country: string;
  identity_country?: string;
  proxy_country?: string;
  geo_country?: string;
  chain_id: string;
  /** 来源: chain=提链自动导入 / manual=手动粘贴 / inventory=重启库存回填 */
  source?: string;
  captcha_type: "iq" | "pi" | "none" | "";
  sms_phone: string;
  sms_price?: number;
  sms_provider_id?: string;
  last_msg?: string;
  last_level?: string;
  error: string;
  created_at: number;
  updated_at: number;
}

/** 接码报价条目 */
export interface SMAQuote {
  provider_id: string;
  price: number;
  count: number;
  currency: string;
  service: string;
}

/** BA 授权配置 */
export interface BAAuthConfig {
  sms_provider: string;
  sms_api_key?: string; // 接码平台 API key (留空回落 .env)
  sms_price: string; // 语义: 单号最高预算 (USD), 授权时自动选最低价供应商
  sms_timeout: number;
  exit_country: string; // 兼容保留 (跟随出口国)
  identity_country?: string; // 表单国家 (默认跟随队列 record.country=提链出口国)
  sms_country?: string; // 接码国家 (默认跟随 identity_country)
  proxy_type: string;
  captcha_strategy: string;
  buyer_mode?: string;
  max_retries: number;
  max_flow_attempts?: number; // 最大流程尝试轮数 (授权整体重试)
  follow_chain_country?: boolean; // 默认 true: 授权国家跟随提链国家
  fail_fast_geo?: boolean; // 默认 true: 代理出口国家与表单国家不一致即失败
  max_concurrent?: number; // 授权段并发上限
  flow_timeout_s?: number; // 单条授权流程超时 (秒), 默认 120
}

/** BA 授权监控日志条目 (全局 store, 切换分栏/重挂载不丢失) */
export interface BAFeedItem {
  ts: number;
  token: string;
  level: "ok" | "info" | "warn" | "err";
  msg: string;
}

/** BA 记录轮询快照 (用于 feed 增量对比) */
export interface BABaSnap {
  status: string;
  step: string;
  error: string;
  source: string;
  last_msg: string;
}

/** 视图名称 */
export type ViewName =
  | "overview" | "chains" | "logs"
  | "tokens" | "proxy" | "inventory"
  | "momo" | "grok" | "pix" | "paypal" | "paypal_extract"
  | "ideal" | "upi" | "kakao" | "blik" | "twint" | "direct"
  | "bizum" | "gopay" | "naver_pay"
  | "gcash" | "grabpay" | "qris"
  | "direct_pay"
  | "analytics" | "samples" | "settings";

/* ==========================================================================
   提链分支 (PayPal 提炼 / MoMo 提链 / Grok 链路 / PIX 二维码)
   各分支独立: 七段设置 / 支付渠道校验 / token 库 / 产出
   ========================================================================== */
export const BRANCH_NAMES = ["paypal", "momo", "grok", "pix", "ideal", "upi", "kakao", "blik", "twint", "direct",
  "bizum", "gopay", "naver_pay", "gcash", "grabpay", "qris"] as const;
export type BranchName = typeof BRANCH_NAMES[number];

export const BRANCH_CN: Record<BranchName, string> = {
  paypal: "PayPal 提炼",
  momo: "MoMo 提链",
  grok: "Grok 链路",
  pix: "PIX 二维码",
  ideal: "iDEAL 提链",
  upi: "UPI 提链",
  kakao: "Kakao Pay 提链",
  blik: "BLIK 提链",
  twint: "TWINT 提链",
  direct: "直卡提链",
  bizum: "Bizum 提链",
  gopay: "GoPay 提链",
  naver_pay: "Naver Pay 提链",
  gcash: "GCash 提链",
  grabpay: "GrabPay 提链",
  qris: "QRIS 提链",
};

export interface StageCfg {
  countries: string[];
  timeout: number;
  retry: number;
  poll_interval?: number;
  max_polls?: number;
}

export interface BranchCfg {
  name: BranchName;
  label: string;
  channel: string;         // 支付渠道校验目标: paypal / momo / card / link
  token_source: string;    // token 库来源标签
  require_zero: boolean;   // 金额校验
  channel_check: boolean;  // 支付渠道校验
  dual_init: boolean;      // 双 init (init0 借道 -> init1 验真 -> init_t 过渡)
  init0_ccs: string[];     // init0 借道出口
  init1_ccs: string[];     // init1 验真出口
  init_t_ccs: string[];    // 过渡出口
  follow_checkout: boolean;// 分段跟随: 除 update 外所有段跟随 checkout
  billing_country: string; // 账单国: "auto"=跟随 checkout 段, 否则固定国家
  attempts: number; // 总尝试 (每 Token 最大尝试轮数)
  stages: Partial<Record<StageName, StageCfg>>;
  /** oaics custom Checkout 五段子配置 */
  oaics?: OaicsBranchCfg;
}

export interface OaicsBranchCfg {
  label: string;
  billing_country: string; // oaics 账单国: "auto"=跟随 checkout 段
  attempts: number;        // oaics 每 Checkout 最大尝试轮数
  stages: Partial<Record<OaicsStageName, StageCfg>>;
}
