import type { ReactNode } from "react";

interface StatusBadgeProps {
  tone: string;
  children: ReactNode;
  dot?: boolean;
  className?: string;
}

export function StatusBadge({ tone, children, dot = true, className = "" }: StatusBadgeProps) {
  return (
    <span className={`status-badge tone-${tone} ${className}`.trim()}>
      {dot && <span className="status-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
