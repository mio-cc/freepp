import { useWebSocket } from "./hooks/useWebSocket";
import { useStore } from "./store/useStore";
import { TitleBar } from "./components/layout/TitleBar";
import { Sidebar } from "./components/layout/Sidebar";
import { OverviewView } from "./views/OverviewView";
import { ChainsView } from "./views/ChainsView";
import { LogsView } from "./views/LogsView";
import { TokensView } from "./views/TokensView";
import { ProxyView } from "./views/ProxyView";
import { InventoryView } from "./views/InventoryView";
import { MomoView } from "./views/MomoView";
import { GrokView } from "./views/GrokView";
import { PixView } from "./views/PixView";
import { BranchConfigView } from "./views/BranchConfigView";
import { DirectView } from "./views/DirectView";
import { PayPalView } from "./views/PayPalView";
import { DirectPayView } from "./views/DirectPayView";
import { PayPalExtractView } from "./views/PayPalExtractView";
import { AnalyticsView } from "./views/AnalyticsView";
import { SamplesView } from "./views/SamplesView";
import { SettingsView } from "./views/SettingsView";

export default function App() {
  useWebSocket();
  const view = useStore((s) => s.currentView);

  return (
    <div className="window">
      <TitleBar />
      <div className="body">
        <Sidebar />
        <main className="content">
          {view === "overview" && <OverviewView />}
          {view === "chains" && <ChainsView />}
          {view === "logs" && <LogsView />}
          {view === "tokens" && <TokensView />}
          {view === "proxy" && <ProxyView />}
          {view === "inventory" && <InventoryView />}
          {view === "momo" && <MomoView />}
          {view === "grok" && <GrokView />}
          {view === "pix" && <PixView />}
          {view === "ideal" && (
            <BranchConfigView branchName="ideal" title="iDEAL 提链" sub="七段出口配置 (iDEAL 渠道) · NL 账单 EUR" defaultCountry="NL" updateCountry="VN" />
          )}
          {view === "upi" && (
            <BranchConfigView branchName="upi" title="UPI 提链" sub="七段出口配置 (UPI 渠道) · IN 账单 INR" defaultCountry="IN" updateCountry="VN" />
          )}
          {view === "kakao" && (
            <BranchConfigView branchName="kakao" title="Kakao Pay 提链" sub="七段出口配置 (Kakao 渠道) · KR 账单 KRW" defaultCountry="KR" updateCountry="VN" />
          )}
          {view === "blik" && (
            <BranchConfigView branchName="blik" title="BLIK 提链" sub="七段出口配置 (BLIK 渠道) · PL 账单 PLN" defaultCountry="PL" updateCountry="PL" />
          )}
          {view === "twint" && (
            <BranchConfigView branchName="twint" title="TWINT 提链" sub="七段出口配置 (TWINT 渠道) · CH 账单 CHF" defaultCountry="CH" updateCountry="VN" />
          )}
          {view === "bizum" && (
            <BranchConfigView branchName="bizum" title="Bizum 提链" sub="七段出口配置 (Bizum 渠道) · ES 账单 EUR · 手机授权提链" defaultCountry="ES" updateCountry="VN" />
          )}
          {view === "gopay" && (
            <BranchConfigView branchName="gopay" title="GoPay 提链" sub="七段出口配置 (GoPay 渠道) · ID 账单 IDR · Midtrans 落地" defaultCountry="ID" updateCountry="VN" />
          )}
          {view === "naver_pay" && (
            <BranchConfigView branchName="naver_pay" title="Naver Pay 提链" sub="七段出口配置 (Naver Pay 渠道) · KR 账单 KRW · NicePay 落地" defaultCountry="KR" updateCountry="VN" />
          )}
          {view === "gcash" && (
            <BranchConfigView branchName="gcash" title="GCash 提链" sub="七段出口配置 (GCash 渠道) · PH 账单 PHP · Adyen 落地" defaultCountry="PH" updateCountry="VN" />
          )}
          {view === "grabpay" && (
            <BranchConfigView branchName="grabpay" title="GrabPay 提链" sub="七段出口配置 (GrabPay 渠道) · PH 账单 PHP · Grab 落地" defaultCountry="PH" updateCountry="VN" />
          )}
          {view === "qris" && (
            <BranchConfigView branchName="qris" title="QRIS 提链" sub="七段出口配置 (QRIS 渠道) · ID 账单 IDR · Midtrans Charge" defaultCountry="ID" updateCountry="VN" />
          )}
          {view === "direct" && <DirectView />}
          {view === "paypal" && <PayPalView />}
          {view === "direct_pay" && <DirectPayView />}
          {view === "paypal_extract" && <PayPalExtractView />}
          {view === "analytics" && <AnalyticsView />}
          {view === "samples" && <SamplesView />}
          {view === "settings" && <SettingsView />}
        </main>
      </div>
    </div>
  );
}
