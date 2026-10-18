import {
  BLOOM_LABELS,
  BLOOM_LEVELS,
  higherOrderShare,
  type BloomDistribution,
  type BloomLevel,
} from "@/lib/assessments/draft";
import { cn } from "@/lib/utils";

const COLORS: Record<BloomLevel, { swatch: string; stroke: string }> = {
  remember: { swatch: "bg-bloom-remember", stroke: "var(--bloom-remember)" },
  understand: { swatch: "bg-bloom-understand", stroke: "var(--bloom-understand)" },
  apply: { swatch: "bg-bloom-apply", stroke: "var(--bloom-apply)" },
  analyze: { swatch: "bg-bloom-analyze", stroke: "var(--bloom-analyze)" },
  evaluate: { swatch: "bg-bloom-evaluate", stroke: "var(--bloom-evaluate)" },
  create: { swatch: "bg-bloom-create", stroke: "var(--bloom-create)" },
};

/** The small dot that marks a level's colour beside its name. */
export function BloomSwatch({ level }: { level: BloomLevel }) {
  return <span aria-hidden className={cn("size-2.5 shrink-0 rounded-full", COLORS[level].swatch)} />;
}

const RADIUS = 38;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

type BloomChartProps = {
  /** Each level's percentage. NaN (a box that isn't a number yet) counts as 0. */
  distribution: BloomDistribution;
  /** A smaller ring and legend, e.g. on the plan page. */
  compact?: boolean;
  className?: string;
};

/**
 * A ring split into the six levels' shares, with a legend and the share of
 * higher-order thinking. While the shares add up to less than 100%, the rest of
 * the ring stays empty; above 100%, they are scaled to fit (the legend always
 * shows each share's own percentage).
 */
export function BloomChart({ distribution, compact = false, className }: BloomChartProps) {
  const shares = BLOOM_LEVELS.map((level) => ({
    level,
    percentage: Number.isFinite(distribution[level]) ? Math.max(0, distribution[level]) : 0,
  }));
  const total = shares.reduce((sum, { percentage }) => sum + percentage, 0);
  const scale = Math.max(100, total);
  // Each share's arc: its length, and where it starts (after the shares before it).
  const arcs = shares.map(({ level, percentage }, index) => ({
    level,
    length: (percentage / scale) * CIRCUMFERENCE,
    start: (shares.slice(0, index).reduce((sum, share) => sum + share.percentage, 0) / scale) * CIRCUMFERENCE,
  }));

  return (
    <div className={cn("flex flex-wrap items-center gap-x-6 gap-y-4", className)}>
      <div className={cn("relative shrink-0", compact ? "size-24" : "size-36")}>
        <svg viewBox="0 0 100 100" aria-hidden className="size-full -rotate-90">
          <circle cx="50" cy="50" r={RADIUS} fill="none" strokeWidth="14" className="stroke-muted" />
          {arcs
            .filter(({ length }) => length > 0)
            .map(({ level, length, start }) => (
              <circle
                key={level}
                cx="50"
                cy="50"
                r={RADIUS}
                fill="none"
                strokeWidth="14"
                stroke={COLORS[level].stroke}
                // A thin gap keeps neighbouring shares apart.
                strokeDasharray={`${Math.max(length - 0.8, 0.4)} ${CIRCUMFERENCE}`}
                strokeDashoffset={-start}
              />
            ))}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={cn("font-semibold tabular-nums", compact ? "text-sm" : "text-xl")}>{total}%</span>
          {!compact && <span className="text-xs text-muted-foreground">Total</span>}
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <ul className={cn("grid gap-x-4 gap-y-1", compact ? "grid-cols-2 text-xs" : "text-sm")}>
          {shares.map(({ level, percentage }) => (
            <li key={level} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-2">
                <BloomSwatch level={level} />
                {BLOOM_LABELS[level]}
              </span>
              <span className="tabular-nums">{percentage}%</span>
            </li>
          ))}
        </ul>
        <p
          className={cn(
            "rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
            compact && "bg-transparent p-0 dark:bg-transparent",
          )}
        >
          <span className="font-semibold">Higher-order thinking: {higherOrderShare(distribution)}%</span>{" "}
          <span className="opacity-80">(Analyze + Evaluate + Create)</span>
        </p>
      </div>
    </div>
  );
}
