import type { InputHTMLAttributes, TextareaHTMLAttributes, SelectHTMLAttributes, ReactNode } from "react";
import clsx from "clsx";
import css from "./Input.module.css";

/**
 * Input 原语 — 包 .input/.select/.textarea 三合一, focus 时 brand 色边框 + glow。
 * 引用别名层 token, 暗色自动适配。
 */
interface InputProps {
  variant?: "input" | "select" | "textarea";
  icon?: ReactNode;
  className?: string | undefined;
}

export function Input({
  variant = "input",
  icon,
  className,
  ...rest
}: InputProps &
  (InputHTMLAttributes<HTMLInputElement> &
    SelectHTMLAttributes<HTMLSelectElement> &
    TextareaHTMLAttributes<HTMLTextAreaElement>)) {
  if (variant === "textarea") {
    return (
      <textarea className={clsx(css.field, css.textarea, className)} {...(rest as TextareaHTMLAttributes<HTMLTextAreaElement>)} />
    );
  }
  if (variant === "select") {
    return (
      <select className={clsx(css.field, className)} {...(rest as SelectHTMLAttributes<HTMLSelectElement>)} />
    );
  }
  return (
    <span className={css.wrap}>
      {icon != null && <span className={css.icon}>{icon}</span>}
      <input className={clsx(css.field, css.input, className)} {...(rest as InputHTMLAttributes<HTMLInputElement>)} />
    </span>
  );
}
