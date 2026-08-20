import type { ReactNode } from "react";
import clsx from "clsx";
import css from "./Badge.module.css";

export type BadgeVariant = "ok" | "warn" | "danger" | "info" | "neutral";

interface BadgeProps {
  variant?: BadgeVariant;
  className?: string | undefined;
  children?: ReactNode;
}

/**
 * Badge 原语 — 状态徽标, 引用别名层状态色 token, 暗色自动适配。
 * 替代现有 .badge-success/.badge-warn 等全局类。
 */
export function Badge({ variant = "neutral", className, children }: BadgeProps) {
  return (
    <span className={clsx(css.badge, css[variant], className)}>{children}</span>
  );
}
