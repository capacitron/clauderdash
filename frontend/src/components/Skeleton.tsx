export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-1 flex-col gap-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-6 animate-pulse rounded-md bg-surface-2/60"
          style={{ animationDelay: `${i * 80}ms` }}
        />
      ))}
    </div>
  );
}

export function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-border/60 py-8 text-center text-xs text-text-subtle">
      {label}
    </div>
  );
}
