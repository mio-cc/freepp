import clsx from "clsx";
import css from "./StateDot.module.css";

export type DotState = "green" | "grey" | "orange" | "red" | "blue";

interface StateDotProps {
  state: DotState;
  className?: string | undefined;
  /** 脉动 (运行态), 受 reduced-motion 守护 */
  pulse?: boolean;
}

/**
 * StateDot 原语 — 状态指示点。
 * 替代现有 .ind-green/.ind-grey 等全局类 (配合 .ind 容器)。
 */
export function StateDot({ state, className, pulse = false }: StateDotProps) {
  return (
    <span
      className={clsx(css.dot, css[state], pulse && css.pulse, className)}
      role="status"
    />
  );
}
