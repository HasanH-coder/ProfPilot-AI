import { DIFFICULTIES, type Difficulty } from "@/lib/assessments/draft";
import { cn } from "@/lib/utils";

const COLORS: Record<Difficulty, string> = {
  easy: "bg-difficulty-easy",
  medium: "bg-difficulty-medium",
  hard: "bg-difficulty-hard",
};

/** The small square that marks a difficulty's colour beside its name. */
export function DifficultySwatch({ difficulty }: { difficulty: Difficulty }) {
  return <span aria-hidden className={cn("size-2.5 shrink-0 rounded-[3px]", COLORS[difficulty])} />;
}

type DifficultyBarProps = {
  /** Each difficulty's percentage. NaN (a box that isn't a number yet) counts as 0. */
  percentages: Record<Difficulty, number>;
  className?: string;
};

/**
 * A bar split into the easy, medium and hard shares. While they add up to less
 * than 100%, the rest of the bar stays empty; above 100%, the shares are scaled
 * to fit. Decorative: the percentages are always shown as text beside it.
 */
export function DifficultyBar({ percentages, className }: DifficultyBarProps) {
  const shares = DIFFICULTIES.map((difficulty) => ({
    difficulty,
    percentage: Number.isFinite(percentages[difficulty]) ? Math.max(0, percentages[difficulty]) : 0,
  })).filter(({ percentage }) => percentage > 0);
  const scale = Math.max(100, shares.reduce((total, { percentage }) => total + percentage, 0));

  return (
    <div aria-hidden className={cn("flex h-2.5 w-full overflow-hidden rounded-full bg-muted", className)}>
      {shares.map(({ difficulty, percentage }) => (
        <div
          key={difficulty}
          // A thin gap in the page colour keeps neighbouring shares apart.
          className={cn("h-full border-background not-first:border-l-2", COLORS[difficulty])}
          style={{ width: `${(percentage / scale) * 100}%` }}
        />
      ))}
    </div>
  );
}
