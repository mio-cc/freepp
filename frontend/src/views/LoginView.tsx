import { useState, useRef, useEffect } from "react";
import { useStore } from "../store/useStore";

/**
 * 登录页 — 全屏居中, 密码验证。
 * 首次启动会在后端终端 (uvicorn stderr) 打印随机密码;
 * 登入后可在「系统 → 密钥与凭据」面板修改密码。
 */
export function LoginView() {
  const login = useStore((s) => s.login);
  const authError = useStore((s) => s.authError);
  const authLoading = useStore((s) => s.authLoading);
  const setAuthError = useStore((s) => s.setAuthError);
  const [password, setPassword] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // 挂载即聚焦密码输入框
  useEffect(() => {
    inputRef.current?.focus();
    // 清掉上次残留错误
    setAuthError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password || authLoading) return;
    await login(password);
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background:
          "radial-gradient(1100px 460px at 70% -10%, rgba(59, 102, 217, 0.06), transparent 60%), var(--bg-app)",
      }}
    >
      <div
        className="card"
        style={{ width: "min(380px, 92vw)", padding: 0 }}
      >
        <div className="card-head" style={{ justifyContent: "center", borderBottom: "none", paddingBottom: 0 }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: "var(--accent)",
                position: "relative",
                flexShrink: 0,
              }}
            >
              <div
                style={{
                  position: "absolute",
                  inset: 10,
                  borderRadius: 5,
                  background: "var(--text-invert)",
                  opacity: 0.9,
                }}
              />
            </div>
            <div style={{ fontSize: 16, fontWeight: 650, letterSpacing: "-0.01em", color: "var(--text-1)" }}>
              Min-Implant v2
            </div>
          </div>
        </div>
        <form className="card-body" style={{ padding: "8px 24px 24px" }} onSubmit={handleSubmit}>
          <div className="field" style={{ marginBottom: 14 }}>
            <label className="field-label" htmlFor="login-password">
              登录密码
            </label>
            <input
              id="login-password"
              ref={inputRef}
              className="input"
              type="password"
              autoComplete="current-password"
              placeholder="请输入密码"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (authError) setAuthError("");
              }}
              disabled={authLoading}
            />
          </div>

          {authError && (
            <div
              style={{
                marginBottom: 12,
                padding: "8px 11px",
                borderRadius: "var(--r-sm)",
                background: "var(--danger-soft)",
                color: "var(--fg-danger)",
                fontSize: 12,
                lineHeight: 1.5,
              }}
            >
              {authError}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-lg"
            style={{ width: "100%" }}
            disabled={authLoading || !password}
          >
            {authLoading ? "登录中…" : "登录"}
          </button>

          <div
            style={{
              marginTop: 16,
              paddingTop: 14,
              borderTop: "1px solid var(--border-faint)",
              fontSize: 11.5,
              color: "var(--text-3)",
              lineHeight: 1.6,
              textAlign: "center",
            }}
          >
            首次启动时, 随机密码会打印在后端终端 (uvicorn stderr)。
            <br />
            登入后可在「系统 → 密钥与凭据」修改密码。
          </div>
        </form>
      </div>
    </div>
  );
}
