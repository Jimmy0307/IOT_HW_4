import { createContext, useContext, type ReactNode } from "react";
import { levelClass, type Level } from "../lib/status";

/** When true, every badge below renders gray: the data on screen is no longer live. */
export const StaleContext = createContext(false);

interface BadgeProps {
  level: Level;
  children: ReactNode;
  testId?: string;
  title?: string;
  variant?: "solid" | "soft";
  className?: string;
}

/** Coloured status pill. Colour is always paired with a text label (accessibility). */
export function Badge({ level, children, testId, title, variant = "soft", className }: BadgeProps) {
  const stale = useContext(StaleContext);
  const effective: Level = stale ? "stale" : level;
  const classes = ["badge", `badge-${variant}`, levelClass(effective), className].filter(Boolean).join(" ");
  return (
    <span className={classes} data-level={effective} data-testid={testId} title={title}>
      <span className="badge-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

export function useEffectiveLevel(level: Level): Level {
  return useContext(StaleContext) ? "stale" : level;
}
