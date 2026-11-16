import type { ReadingView } from "../api/types";
import { formatNumber } from "../lib/format";
import { readingLevel } from "../lib/status";
import { useEffectiveLevel } from "./Badge";

interface ReadingProps {
  label: string;
  unit: string;
  reading: ReadingView | undefined;
  testId: string;
  size?: "md" | "lg";
  hint?: string;
}

/** Big numeric value; a stale value stays visible but gray with a STALE tag. */
export function Reading({ label, unit, reading, testId, size = "md", hint }: ReadingProps) {
  const level = useEffectiveLevel(readingLevel(reading?.level));
  const value = reading?.value ?? null;
  const tag = !reading || reading.display === "UNKNOWN" ? "NO DATA" : reading.fresh ? reading.level.toUpperCase() : "STALE";
  return (
    <div className={`reading reading-${size} lvl-${level}`} data-level={level}>
      <span className="reading-label">{label}</span>
      <span className="reading-value" data-testid={testId} data-level={level}>
        {formatNumber(value)}
        <span className="reading-unit">{unit}</span>
        <span className="reading-tag">{tag}</span>
      </span>
      {hint ? <span className="reading-hint">{hint}</span> : null}
    </div>
  );
}
