import { relativeTime } from "../lib/format";

export function RelativeTime({ ts }: { ts: string | number | null | undefined }) {
  const absolute = ts
    ? typeof ts === "number"
      ? new Date(ts * 1000).toLocaleString()
      : new Date(ts).toLocaleString()
    : "—";
  return (
    <time title={absolute} className="font-mono text-[10px] uppercase tracking-wider text-text-subtle">
      {relativeTime(ts)}
    </time>
  );
}
