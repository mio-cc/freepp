import { useState } from "react";
import { useStore } from "../../store/useStore";
import type { ViewName } from "../../types";

const COLLAPSE_KEY = "min.sidebar.collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

const NAV_GROUPS: { label: string; items: { view: ViewName; icon: string; text: string }[] }[] = [
  {
    label: "监控",
    items: [
      { view: "overview", icon: "grid", text: "总览" },
      { view: "pipeline", icon: "bolt", text: "一键流程" },
      { view: "chains", icon: "chains", text: "链路监控" },
      { view: "logs", icon: "logs", text: "实时日志" },
    ],
  },
  {
    label: "资源",
    items: [
      { view: "tokens", icon: "token", text: "Token 库" },
      { view: "proxy", icon: "proxy", text: "代理池" },
      { view: "inventory", icon: "inventory", text: "成功库存" },
      { view: "register", icon: "register", text: "账号注册" },
      { view: "mailpool", icon: "mail", text: "邮箱池" },
    ],
  },
  {
    label: "链路配置",
    items: [
      { view: "paypal_extract", icon: "paypal", text: "PayPal 提炼" },
      { view: "momo", icon: "momo", text: "MoMo 提链" },
      { view: "grok", icon: "grok", text: "Grok 链路" },
      { view: "pix", icon: "pix", text: "PIX 二维码" },
      { view: "ideal", icon: "ideal", text: "iDEAL 提链" },
      { view: "upi", icon: "upi", text: "UPI 提链" },
      { view: "kakao", icon: "kakao", text: "Kakao Pay" },
      { view: "blik", icon: "blik", text: "BLIK 提链" },
      { view: "twint", icon: "twint", text: "TWINT 提链" },
      { view: "bizum", icon: "bizum", text: "Bizum 提链" },
      { view: "gopay", icon: "gopay", text: "GoPay 提链" },
      { view: "naver_pay", icon: "naver_pay", text: "Naver Pay" },
      { view: "gcash", icon: "gcash", text: "GCash 提链" },
      { view: "grabpay", icon: "grabpay", text: "GrabPay 提链" },
      { view: "qris", icon: "qris", text: "QRIS 提链" },
      { view: "direct", icon: "direct", text: "直卡提链" },
    ],
  },
  {
    label: "支付授权",
    items: [
      { view: "paypal", icon: "paypal", text: "PayPal 授权" },
      { view: "direct_pay", icon: "direct", text: "直卡支付" },
    ],
  },
  {
    label: "分析",
    items: [
      { view: "analytics", icon: "analytics", text: "统计分析" },
      { view: "samples", icon: "samples", text: "样本记录" },
    ],
  },
  {
    label: "系统",
    items: [
      { view: "secrets", icon: "key", text: "密钥与凭据" },
      { view: "settings", icon: "settings", text: "设置" },
    ],
  },
];

const ICONS: Record<string, string> = {
  grid: `<rect x="1.5" y="1.5" width="5.5" height="5.5" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="9" y="1.5" width="5.5" height="5.5" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="1.5" y="9" width="5.5" height="5.5" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="9" y="9" width="5.5" height="5.5" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.1"/>`,
  chains: `<path d="M2 4h3l2 2 2-2h5M2 8h3l2 2 2-2h5M2 12h3l2 2 2-2h5" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>`,
  logs: `<rect x="1.5" y="2" width="13" height="12" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.1"/><line x1="4" y1="5.5" x2="12" y2="5.5" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><line x1="4" y1="8" x2="10" y2="8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><line x1="4" y1="10.5" x2="8" y2="10.5" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  token: `<circle cx="8" cy="5.5" r="2.6" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M2.5 13.6a5.5 5.5 0 0 1 11 0" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  proxy: `<circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M2 8h12M8 2c2.2 2.2 2.2 9.8 0 12M8 2c-2.2 2.2-2.2 9.8 0 12" fill="none" stroke="currentColor" stroke-width="1.1"/>`,
  inventory: `<path d="M2 4l6-2.2L14 4v8L8 14.2 2 12V4z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><path d="M2 4l6 2.2L14 4M8 6.2V14" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>`,
  register: `<circle cx="5.5" cy="5" r="2.2" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M2 12.5a3.5 3.5 0 0 1 7 0" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><path d="M11 4.5l2 2M13 2l-3.4 3.4 2 2L15 4l-2-2z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><line x1="11.4" y1="6.4" x2="13.4" y2="8.4" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  mail: `<rect x="1.5" y="3" width="13" height="10" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M1.8 4L8 8.5 14.2 4" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>`,
  momo: `<rect x="2" y="3" width="12" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.1"/><circle cx="5.5" cy="8" r="1.2" fill="currentColor"/><circle cx="10.5" cy="8" r="1.2" fill="currentColor"/>`,
  grok: `<path d="M8 2L3 14h2.5L8 8l2.5 6H13L8 2z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>`,
  pix: `<rect x="2" y="2" width="5" height="5" rx="0.8" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="9" y="2" width="5" height="5" rx="0.8" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="2" y="9" width="5" height="5" rx="0.8" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="9.5" y="9.5" width="1.5" height="1.5" fill="currentColor"/><rect x="12" y="9.5" width="1.5" height="1.5" fill="currentColor"/><rect x="9.5" y="12" width="1.5" height="1.5" fill="currentColor"/>`,
  ideal: `<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M8 2.5v11M4 8h8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  upi: `<path d="M2.5 5h11M2.5 8h11M2.5 11h11" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><path d="M4 5l-1.5 3L4 11" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>`,
  kakao: `<path d="M8 2.5c-3 0-5.5 2.2-5.5 5 0 1.9 1.3 3.5 3.2 4.4l-.8 2.6 2.9-1.7c.7.2 1.4.3 2.2.3 3 0 5.5-2.2 5.5-5s-2.5-5-5.5-5z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>`,
  blik: `<rect x="3" y="2.5" width="10" height="7" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M5 9.5v3h6v-3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  twint: `<rect x="2" y="2" width="12" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M5 11l2-4 2 2.5L11 5l1 6" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>`,
  bizum: `<path d="M2 5.5h12l-1.6 7H3.6L2 5.5z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><path d="M5.5 4.5L8 2l2.5 2.5" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/><path d="M6.5 8h3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  gopay: `<path d="M2.5 10.5l3-6 2.5 4.5 2-3.5 3.5 5H2.5z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><circle cx="11.5" cy="3.5" r="1.4" fill="currentColor"/>`,
  naver_pay: `<path d="M2 4h12l-1 8H4L2 4z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><path d="M5.5 8.5V6M10.5 8.5V6M8 10.5v-1" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  gcash: `<circle cx="6" cy="8" r="4.2" fill="none" stroke="currentColor" stroke-width="1.1"/><circle cx="10" cy="8" r="4.2" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M8 3.8v8.4" stroke="currentColor" stroke-width="1.1"/>`,
  grabpay: `<path d="M2 5.5h12l-1.2 7.5H3.2L2 5.5z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><path d="M5 5.5L6.5 2h3L11 5.5" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>`,
  qris: `<rect x="2" y="2" width="4.5" height="4.5" rx="0.8" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="9.5" y="2" width="4.5" height="4.5" rx="0.8" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="2" y="9.5" width="4.5" height="4.5" rx="0.8" fill="none" stroke="currentColor" stroke-width="1.1"/><rect x="10" y="10" width="1.4" height="1.4" fill="currentColor"/><rect x="12.6" y="10" width="1.4" height="1.4" fill="currentColor"/><rect x="10" y="12.6" width="1.4" height="1.4" fill="currentColor"/><rect x="12.6" y="12.6" width="1.4" height="1.4" fill="currentColor"/>`,
  direct: `<path d="M2 3.5h12M2 8h12M2 12.5h12" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><circle cx="6" cy="5.5" r="1.2" fill="currentColor"/><circle cx="10" cy="10" r="1.2" fill="currentColor"/>`,
  paypal: `<path d="M4 2h6.5c2 0 3.5 1.3 3.5 3.3 0 2.3-1.8 3.7-4.2 3.7H8.2L7.5 14H5l1.5-9.5H4V2z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><path d="M3.5 3.5H2" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  analytics: `<path d="M2 13V3M2 13h12" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><rect x="4" y="9" width="2.5" height="4" fill="currentColor" opacity="0.6"/><rect x="7.5" y="6" width="2.5" height="7" fill="currentColor" opacity="0.6"/><rect x="11" y="8" width="2.5" height="5" fill="currentColor" opacity="0.6"/>`,
  samples: `<path d="M3 2h7l3 3v9H3V2z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><path d="M10 2v3h3" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><line x1="5" y1="8" x2="11" y2="8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><line x1="5" y1="10.5" x2="9" y2="10.5" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  settings: `<circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5L13 13M3 13l1.5-1.5M11.5 4.5L13 3" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
  key: `<circle cx="5" cy="8" r="3.2" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M7.5 8h6M11 8v2M13 8v1.5" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>`,
  bolt: `<path d="M8 1.5L3.5 9h4L6.5 14.5 12.5 7h-4L9.5 1.5z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>`,
};

export function Sidebar() {
  const currentView = useStore((s) => s.currentView);
  const setView = useStore((s) => s.setView);
  const tokens = useStore((s) => s.tokens);
  const nodes = useStore((s) => s.nodes);
  const chainStates = useStore((s) => s.chainStates);
  const stats = useStore((s) => s.stats);
  const [collapsed, setCollapsed] = useState<boolean>(readCollapsed);
  // 折叠动画状态机 (对齐 SidebarRoot.tsx):
  //  折叠: fading(宽内容淡出 150ms) → settled 后切 collapsed
  //  展开: 移除 collapsed → wide-in(宽内容淡入 200ms)
  const [fading, setFading] = useState(false);
  const [wideIn, setWideIn] = useState(false);

  const toggleCollapsed = () => {
    if (collapsed) {
      // 展开: 先取消 collapsed (rail 滑出), 再触发 wide-in 淡入
      setCollapsed(false);
      setWideIn(true);
      window.setTimeout(() => setWideIn(false), 220);
    } else {
      // 折叠: 先 fading 淡出宽内容, settle 后切 collapsed
      setFading(true);
      window.setTimeout(() => {
        setFading(false);
        setCollapsed(true);
        try {
          localStorage.setItem(COLLAPSE_KEY, "1");
        } catch {
          /* ignore */
        }
      }, 150);
      return;
    }
    try {
      localStorage.setItem(COLLAPSE_KEY, "0");
    } catch {
      /* ignore */
    }
  };

  const activeChains = Object.values(chainStates).filter((c) => c.status === "running").length;
  const totalSuccess = stats.success || 0;
  const totalFail = stats.failure || 0;
  const total = totalSuccess + totalFail;
  const rate = total > 0 ? ((totalSuccess / total) * 100).toFixed(0) : "—";

  // 从成功链路中统计待授权 BA 数量
  const pendingBa = Object.values(chainStates).filter(
    (c) => c.status === "success" && c.url && c.url.includes("ba_token=BA-")
  ).length;

  // 折叠态下有活动计数的项在图标右上角显示小圆点
  const dots: Partial<Record<ViewName, boolean>> = {
    chains: activeChains > 0,
    paypal: pendingBa > 0,
  };

  const counts: Partial<Record<ViewName, { text: string; cls?: string }>> = {
    chains: { text: String(activeChains || ""), cls: activeChains > 0 ? "nav-count-live" : "" },
    tokens: { text: String(tokens.length || "") },
    proxy: { text: String(nodes.length || "") },
    inventory: { text: String(totalSuccess), cls: "nav-count-gold" },
    paypal: { text: String(pendingBa || ""), cls: pendingBa > 0 ? "nav-count-live" : "" },
  };

  return (
    <nav className={`sidebar ${collapsed ? "collapsed" : ""} ${fading ? "fading" : ""} ${wideIn ? "wide-in" : ""}`}>
      <div className="sidebar-brand">
        <span className="brand-mark" />
        <span className="brand-text">控制台</span>
        <button
          type="button"
          className="sidebar-toggle"
          onClick={toggleCollapsed}
          aria-label={collapsed ? "展开侧边栏" : "收窄侧边栏"}
          title={collapsed ? "展开侧边栏" : "收窄侧边栏"}
        >
          <svg viewBox="0 0 16 16" className={collapsed ? "chev-collapsed" : ""} fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 3.5L5.5 8l4.5 4.5" />
          </svg>
        </button>
      </div>
      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="sidebar-group">
          <p className="sidebar-label">{group.label}</p>
          {group.items.map((item) => (
            <a
              key={item.view}
              className={`nav-item ${currentView === item.view ? "active" : ""}`}
              onClick={() => setView(item.view)}
              title={collapsed ? item.text : undefined}
              aria-label={item.text}
            >
              <svg viewBox="0 0 16 16" className="nav-icon" dangerouslySetInnerHTML={{ __html: ICONS[item.icon] || "" }} />
              <span className="nav-text">{item.text}</span>
              {counts[item.view]?.text && !collapsed && (
                <span className={`nav-count ${counts[item.view]?.cls || ""}`}>
                  {counts[item.view]?.text}
                </span>
              )}
              {collapsed && dots[item.view] && <span className="nav-dot" />}
            </a>
          ))}
        </div>
      ))}
      <div className="sidebar-footer">
        <div className="sidebar-stat">
          <span className="ss-label">成功累计</span>
          <span className="ss-value">{totalSuccess}</span>
        </div>
        <div className="sidebar-stat">
          <span className="ss-label">成功率</span>
          <span className="ss-value">{rate}%</span>
        </div>
      </div>
    </nav>
  );
}
