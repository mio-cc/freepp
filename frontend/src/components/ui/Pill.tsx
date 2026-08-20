import type { ReactNode } from "react";
import clsx from "clsx";
import css from "./Pill.module.css";

interface PillProps {
  active?: boolean;
  className?: string | undefined;
  children?: ReactNode;
  onClick?: () => void;
}

/**
 * Pill 原语 — 横向标签, h24/r12。
 * 替代现有 .geo-chip/.tag 等全局类。
 */
export function Pill({ active = false, className, children, onClick }: PillProps) {
  return (
    <span
      className={clsx(css.pill, active && css.active, onClick && css.interactive, className)}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      {children}
    </span>
  );
}
