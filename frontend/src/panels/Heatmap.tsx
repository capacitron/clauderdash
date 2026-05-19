import { useEffect, useMemo, useState } from "react";
import CalendarHeatmap, { HeatmapValue } from "react-calendar-heatmap";
import "react-calendar-heatmap/dist/styles.css";
import { apiGet } from "../lib/api";
import { Panel } from "../components/Panel";
import { Skeleton, EmptyState } from "../components/Skeleton";

type Resp = {
  weeks: number;
  days: { date: string; count: number }[];
};

export function Heatmap() {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setErr(null);
    apiGet<Resp>("/api/heatmap")
      .then(setData)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const countOf = (value: HeatmapValue | undefined): number => {
    if (!value) return 0;
    const raw = (value as { count?: unknown }).count;
    return typeof raw === "number" ? raw : 0;
  };

  const { values, startDate, endDate, maxCount } = useMemo(() => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - (data?.weeks ?? 12) * 7);
    const vals: HeatmapValue[] = (data?.days ?? []).map((d) => ({
      date: d.date,
      count: d.count,
    }));
    const max = vals.reduce((m, v) => Math.max(m, countOf(v)), 0);
    return { values: vals, startDate: start, endDate: end, maxCount: max };
  }, [data]);

  const classFor = (value: HeatmapValue | undefined) => {
    const c = countOf(value);
    if (!c) return "rch-empty";
    const ratio = maxCount ? c / maxCount : 0;
    if (ratio > 0.66) return "rch-q4";
    if (ratio > 0.33) return "rch-q3";
    if (ratio > 0.1) return "rch-q2";
    return "rch-q1";
  };

  const title = (value: HeatmapValue | undefined) => {
    if (!value) return "";
    const c = countOf(value);
    return `${value.date as string} — ${c} message${c === 1 ? "" : "s"}`;
  };

  return (
    <Panel
      kicker="11 · ACTIVITY"
      title="Activity heatmap"
      subtitle={`Messages per day, last ${data?.weeks ?? 12} weeks`}
    >
      {err ? (
        <EmptyState label={`Load failed: ${err}`} />
      ) : !data ? (
        <Skeleton rows={4} />
      ) : data.days.length === 0 ? (
        <EmptyState label="No activity in the past 12 weeks." />
      ) : (
        <div className="flex-1 heatmap-wrap">
          <CalendarHeatmap
            values={values}
            startDate={startDate}
            endDate={endDate}
            classForValue={classFor}
            titleForValue={title}
            showMonthLabels
            showWeekdayLabels={false}
            gutterSize={3}
          />
        </div>
      )}
    </Panel>
  );
}
