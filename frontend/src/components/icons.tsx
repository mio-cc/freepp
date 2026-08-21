// 集中 SVG 图标组件，替代 emoji。
// 全部使用 currentColor + stroke 风格，与既有 NAV_ICON 一致。
// size 预设 1em，可用 className/style 覆盖。

import type { CSSProperties } from "react";

type IconProps = { size?: number; style?: CSSProperties; className?: string };

function svgProps(size: number, style?: CSSProperties, className?: string) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    style,
    className,
    "aria-hidden": true,
  };
}

export function CheckIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M3 8l3 3 7-7" />
    </svg>
  );
}

export function XIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}

export function WarnIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M8 2L1.5 13.5h13L8 2z" />
      <path d="M8 7v3" />
      <circle cx="8" cy="12" r="0.3" fill="currentColor" />
    </svg>
  );
}

export function RefreshIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M13 8a5 5 0 1 1-1.5-3.5" />
      <path d="M13 2v3h-3" />
    </svg>
  );
}

export function MoonIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5 5 0 1 0 6.5 6.5z" />
    </svg>
  );
}

export function LockIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <rect x="3.5" y="7" width="9" height="6.5" rx="1" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
    </svg>
  );
}

export function LinkIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M7 9l2-2a2 2 0 0 1 3 3l-2 2a2 2 0 0 1-3-1" />
      <path d="M9 7L7 9a2 2 0 0 1-3-1l2-2a2 2 0 0 1 3 1" />
    </svg>
  );
}

export function MailIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <rect x="2" y="4" width="12" height="8" rx="1" />
      <path d="M2 5l6 4 6-4" />
    </svg>
  );
}

export function FileIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M3 2h6l4 4v8H3V2z" />
      <path d="M9 2v4h4" />
    </svg>
  );
}

export function StarIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M8 2l1.8 4.2L14 7l-3.5 2.7L11.5 14 8 11.5 4.5 14l1-4.3L2 7l4.2-0.8L8 2z" />
    </svg>
  );
}

export function BoltIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M9 2L3 9h4l-1 5 6-7H8l1-5z" />
    </svg>
  );
}

export function InboxIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M2 9l2-5h8l2 5" />
      <path d="M2 9v3h12V9" />
      <path d="M2 9h3l1 2h4l1-2h3" />
    </svg>
  );
}

export function CreditCardIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <rect x="2" y="3.5" width="12" height="9" rx="1" />
      <path d="M2 7h12" />
      <path d="M5 10.5h2" />
    </svg>
  );
}

export function HourglassIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M4 2h8v2L8 7 4 4V2z" />
      <path d="M4 14h8v-2L8 9l-4 3v2z" />
    </svg>
  );
}

export function SatelliteIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M3 13c2-4 6-6 10-6" />
      <path d="M5 11c1-2 3-3 5-3" />
      <circle cx="11" cy="5" r="1.2" />
      <path d="M9 3l4 4" />
    </svg>
  );
}

export function MonkeyIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <circle cx="8" cy="8" r="5" />
      <path d="M5.5 7.5h0M10.5 7.5h0" />
      <path d="M6 10c0.5 1 3.5 1 4 0" />
      <path d="M3 4c0-1 1-2 2-2M13 4c0-1-1-2-2-2" />
    </svg>
  );
}

export function PrayIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M5 14V7c0-1 1-2 2-2s2 1 2 2v7" />
      <path d="M9 7c0-1 1-2 2-2s2 1 2 2v7" />
      <path d="M5 7c0-1-1-2-2-2s-2 1-2 2v5" />
      <path d="M9 2c0 1-1 2-2 2" />
    </svg>
  );
}

// 箭头组件（用于装饰位置；纯文字箭头 → ← 保留不改）
export function ArrowRightIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M3 8h10M9 4l4 4-4 4" />
    </svg>
  );
}

export function ArrowLeftIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M13 8H3M7 4L3 8l4 4" />
    </svg>
  );
}

export function ArrowUpIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M8 13V3M4 7l4-4 4 4" />
    </svg>
  );
}

export function ArrowDownIcon({ size = 14, style, className }: IconProps) {
  return (
    <svg {...svgProps(size, style, className)}>
      <path d="M8 3v10M4 9l4 4 4-4" />
    </svg>
  );
}
