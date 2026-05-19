import clsx from "clsx";
import { Range } from "../lib/api";

const OPTIONS: { key: Range; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7d" },
  { key: "30d", label: "30d" },
];

export function RangeToggle({
  value,
  onChange,
}: {
  value: Range;
  onChange: (r: Range) => void;
}) {
  return (
    <div className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface-2 p-0.5">
      {OPTIONS.map((opt) => (
        <button
          key={opt.key}
          className={clsx(
            "rounded-md px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider transition-colors",
            value === opt.key
              ? "bg-accent-blue/15 text-accent-blue"
              : "text-text-subtle hover:text-text-dim",
          )}
          onClick={() => onChange(opt.key)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
