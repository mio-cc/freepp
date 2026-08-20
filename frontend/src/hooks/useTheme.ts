import { useEffect } from "react";
import { useStore } from "../store/useStore";

/**
 * 主题管理 hook: 把 store 里的 theme (light|dark|system) 解析成实际主题,
 * 写到 document.documentElement.dataset.theme 上 (tokens.css 的暗色别名挂在 html[data-theme="dark"])。
 *
 * 防闪屏由 index.html 的内联脚本负责 (React 挂载前已设好 dataset.theme);
 * 此 hook 负责 React 挂载后用户切换主题时的同步, 以及 system 模式下跟随系统偏好。
 *
 * 主题持久化 key: localStorage["min.theme"] (与 Sidebar 的 "min.sidebar.collapsed" 命名空间一致)。
 */
const THEME_KEY = "min.theme";
const DARK_MEDIA = "(prefers-color-scheme: dark)";

function resolveDark(pref: "light" | "dark" | "system"): boolean {
  if (pref === "dark") return true;
  if (pref === "light") return false;
  return window.matchMedia(DARK_MEDIA).matches;
}

function applyTheme(pref: "light" | "dark" | "system"): void {
  const dark = resolveDark(pref);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

export function useTheme() {
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);

  // 首次挂载: 若 store 初值与 localStorage 不一致则修正 (防闪屏脚本已设好 DOM, 这里只同步 store)
  useEffect(() => {
    let stored: "light" | "dark" | "system" = "dark";
    try {
      const v = localStorage.getItem(THEME_KEY);
      if (v === "light" || v === "dark" || v === "system") stored = v;
    } catch { /* ignore */ }
    if (stored !== theme) setTheme(stored);
    // 只在挂载时同步一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // theme 变化时: 应用到 DOM + 持久化
  useEffect(() => {
    applyTheme(theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
  }, [theme]);

  // system 模式下跟随系统偏好变化
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia(DARK_MEDIA);
    const handler = () => applyTheme("system");
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [theme]);
}
