import type { TrendPoint } from "../api/types";
import { formatNumber } from "../lib/format";

export const TREND_WINDOW_MS = 10 * 60 * 1000;
/** A gap longer than this between samples breaks the line (missing / stale data). */
const GAP_BREAK_MS = 15_000;

const W = 640;
const H = 190;
const PAD = { left: 44, right: 12, top: 12, bottom: 24 };

interface Series {
  t: number;
  v: number;
}

interface Domain {
  min: number;
  max: number;
}

export function toSeries(points: readonly TrendPoint[] | undefined, startMs: number, endMs: number): Series[] {
  if (!points) return [];
  return points
    .map((p) => ({ t: Date.parse(p.t), v: p.v }))
    .filter((p) => Number.isFinite(p.t) && typeof p.v === "number" && Number.isFinite(p.v))
    .filter((p) => p.t >= startMs && p.t <= endMs)
    .sort((a, b) => a.t - b.t);
}

export function yDomain(values: number[], refs: number[]): Domain {
  const all = [...values, ...refs].filter((n) => Number.isFinite(n));
  if (all.length === 0) return { min: 0, max: 1 };
  let min = Math.min(...all);
  let max = Math.max(...all);
  if (max - min < 1) {
    min -= 0.5;
    max += 0.5;
  }
  const pad = (max - min) * 0.1;
  return { min: min - pad, max: max + pad };
}

/** Build an SVG path, starting a new sub-path ("M") after each data gap. */
export function buildPath(series: Series[], x: (t: number) => number, y: (v: number) => number): string {
  let d = "";
  let prev: number | null = null;
  for (const p of series) {
    const cmd = prev === null || p.t - prev > GAP_BREAK_MS ? "M" : "L";
    d += `${cmd}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)} `;
    prev = p.t;
  }
  return d.trim();
}

interface RefLine {
  value: number;
  label: string;
  kind: "band" | "target" | "critical";
}

interface TrendPanelProps {
  title: string;
  unit: string;
  points: readonly TrendPoint[] | undefined;
  refs: RefLine[];
  endMs: number;
  seriesClass: string;
  stale: boolean;
}

function TrendPanel({ title, unit, points, refs, endMs, seriesClass, stale }: TrendPanelProps) {
  const startMs = endMs - TREND_WINDOW_MS;
  const series = toSeries(points, startMs, endMs);
  // Only include reference lines in the domain if they are reasonably close to the data.
  const values = series.map((p) => p.v);
  const bandRefs = refs.filter((r) => r.kind !== "critical").map((r) => r.value);
  const dom = yDomain(values, bandRefs);
  const visibleRefs = refs.filter((r) => r.value >= dom.min && r.value <= dom.max);

  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (t: number) => PAD.left + ((t - startMs) / TREND_WINDOW_MS) * innerW;
  const y = (v: number) => PAD.top + (1 - (v - dom.min) / (dom.max - dom.min)) * innerH;
  const yTicks = [0, 1, 2, 3].map((i) => dom.min + ((dom.max - dom.min) * i) / 3);
  const xTicks = [10, 8, 6, 4, 2, 0];
  const last = series.length > 0 ? series[series.length - 1] : null;
  const path = buildPath(series, x, y);

  return (
    <figure className={`trend-panel${stale ? " is-stale" : ""}`}>
      <figcaption className="trend-caption">
        <span className="trend-title">{title}</span>
        <span className="trend-last">
          {last ? `latest ${formatNumber(last.v)} ${unit}` : "no validated samples in window"}
        </span>
      </figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="trend-svg"
        role="img"
        aria-label={`${title} over the last 10 minutes, ${series.length} samples`}
      >
        {yTicks.map((v) => (
          <g key={`y${v}`}>
            <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
            <text className="axis-label" x={PAD.left - 6} y={y(v) + 4} textAnchor="end">
              {formatNumber(v)}
            </text>
          </g>
        ))}
        {xTicks.map((m) => {
          const xt = x(endMs - m * 60_000);
          return (
            <text key={`x${m}`} className="axis-label" x={xt} y={H - 6} textAnchor="middle">
              {m === 0 ? "now" : `-${m}m`}
            </text>
          );
        })}
        {visibleRefs.map((r) => (
          <g key={`${r.kind}-${r.label}`}>
            <line className={`ref ref-${r.kind}`} x1={PAD.left} x2={W - PAD.right} y1={y(r.value)} y2={y(r.value)} />
            <text className={`ref-label ref-label-${r.kind}`} x={W - PAD.right - 4} y={y(r.value) - 4} textAnchor="end">
              {`${r.label} ${formatNumber(r.value)}`}
            </text>
          </g>
        ))}
        {path ? <path className={`series ${seriesClass}`} d={path} /> : null}
        {last ? <circle className={`series-dot ${seriesClass}`} cx={x(last.t)} cy={y(last.v)} r={3.5} /> : null}
        {series.length === 0 ? (
          <text className="axis-label" x={W / 2} y={H / 2} textAnchor="middle">
            No validated data in the last 10 minutes
          </text>
        ) : null}
      </svg>
    </figure>
  );
}

interface TrendChartProps {
  temperature: readonly TrendPoint[] | undefined;
  humidity: readonly TrendPoint[] | undefined;
  tempBand: { low: number; high: number } | null;
  humBand: { low: number; high: number } | null;
  tempTarget?: number;
  humTarget?: number;
  interlock?: number;
  endMs: number;
  stale: boolean;
}

export function TrendChart(props: TrendChartProps) {
  const { tempBand, humBand } = props;
  const tempRefs: RefLine[] = [];
  if (tempBand) tempRefs.push({ value: tempBand.high, label: "high", kind: "band" }, { value: tempBand.low, label: "low", kind: "band" });
  if (props.tempTarget !== undefined) tempRefs.push({ value: props.tempTarget, label: "target", kind: "target" });
  if (props.interlock !== undefined) tempRefs.push({ value: props.interlock, label: "interlock", kind: "critical" });
  const humRefs: RefLine[] = [];
  if (humBand) humRefs.push({ value: humBand.high, label: "high", kind: "band" }, { value: humBand.low, label: "low", kind: "band" });
  if (props.humTarget !== undefined) humRefs.push({ value: props.humTarget, label: "target", kind: "target" });

  return (
    <div className="trend-chart" data-testid="trend-chart">
      <TrendPanel
        title="Temperature (°C)"
        unit="°C"
        points={props.temperature}
        refs={tempRefs}
        endMs={props.endMs}
        seriesClass="series-temp"
        stale={props.stale}
      />
      <TrendPanel
        title="Humidity (%RH)"
        unit="%RH"
        points={props.humidity}
        refs={humRefs}
        endMs={props.endMs}
        seriesClass="series-hum"
        stale={props.stale}
      />
      <p className="trend-legend">
        <span className="legend-swatch legend-band" aria-hidden="true" /> dashed amber = control band (high/low)
        <span className="legend-swatch legend-target" aria-hidden="true" /> dotted = target
        <span className="legend-swatch legend-critical" aria-hidden="true" /> red = hard interlock. Gaps over 15 s are not joined.
      </p>
    </div>
  );
}
