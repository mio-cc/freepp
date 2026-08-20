import type { ButtonHTMLAttributes, ReactNode } from "react";
import clsx from "clsx";
import css from "./Button.module.css";

export type ButtonVariant = "primary" | "ghost" | "outline" | "danger" | "success";
export type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  className?: string | undefined;
  children?: ReactNode;
}

/**
 * Button 原语 — 引用别名层 token, 暗色自动适配。
 * 保留 Fluent 方形 (r8) 以维持项目辨识度 (非 deepseek 胶囊形)。
 * 现有全局 .btn / .btn-primary 等类通过过渡别名仍工作, 新代码用此原语。
 */
export function Button({
  variant = "ghost",
  size = "md",
  icon,
  className,
  children,
  ...rest
}: ButtonProps & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={clsx(css.button, css[variant], css[size], className)}
      {...rest}
    >
      {icon != null && <span className={css.icon}>{icon}</span>}
      {children}
    </button>
  );
}
