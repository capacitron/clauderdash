import clsx from "clsx";

// Arc geometry: semicircle from 9 o'clock → 12 o'clock → 3 o'clock
// angle(v) = (180 + v*1.8)° — increasing-angle direction (sweep=1) traces the upper arc
const CX = 100;
const CY = 108;
const R = 76;
const SW = 14;

function pt(v: number): [number, number] {
  const a = (180 + v * 1.8) * (Math.PI / 180);
  return [CX + R * Math.cos(a), CY + R * Math.sin(a)];
}

function arcPath(v1: number, v2: number): string {
  const [x1, y1] = pt(v1);
  const [x2, y2] = pt(v2);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${R} ${R} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

const ZONE_COLOR = {
  green: "#7aab6e",
  amber: "#e8943a",
  red:   "#c96b5a",
} as const;

export type ZoneColor = keyof typeof ZONE_COLOR;

export type ZoneDef = {
  t1: number;
  t2: number;
  colors: readonly [ZoneColor, ZoneColor, ZoneColor];
};

// ── Arc gauge (Burn / Discipline / Efficiency / Hygiene / Live) ──────────────

type Props = {
  kicker: string;
  label: string;
  statDisplay: string;
  detail?: string;
  value: number;
  zone: ZoneDef;
  active?: boolean;
  onClick?: () => void;
};

export function ArcGauge(props: Props) {
  const { kicker, label, detail, active, onClick } = props;

  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={clsx(
        "flex w-full flex-col items-center rounded-2xl border bg-surface px-4 pb-4 pt-3",
        "transition-colors duration-150 focus-visible:outline-none focus-visible:ring-1",
        "focus-visible:ring-accent-cyan",
        active
          ? "border-accent-cyan/50 bg-accent-cyan/[0.04]"
          : "border-border hover:border-border-glow",
      )}
    >
      <div className="kicker mb-1 w-full text-left">{kicker}</div>

      <ArcFace
        value={props.value}
        statDisplay={props.statDisplay}
        zone={props.zone}
        active={active}
      />

      <div className="mt-2 font-sans text-sm font-semibold text-text">{label}</div>

      {detail && (
        <div className="mt-0.5 line-clamp-2 max-w-full px-1 text-center font-mono text-[10px] leading-tight text-text-subtle">
          {detail}
        </div>
      )}
    </button>
  );
}

// ── ArcFace ──────────────────────────────────────────────────────────────────

function ArcFace({
  value,
  statDisplay,
  zone,
  active,
}: {
  value: number;
  statDisplay: string;
  zone: ZoneDef;
  active?: boolean;
}) {
  const v = Math.min(100, Math.max(0, value));
  const needleRot = (v / 100) * 180 - 90;

  const zones = [
    { path: arcPath(0, zone.t1), color: ZONE_COLOR[zone.colors[0]] },
    { path: arcPath(zone.t1, zone.t2), color: ZONE_COLOR[zone.colors[1]] },
    { path: arcPath(zone.t2, 100), color: ZONE_COLOR[zone.colors[2]] },
  ];

  return (
    <>
      <svg
        viewBox="0 0 200 120"
        xmlns="http://www.w3.org/2000/svg"
        className="w-full"
        aria-hidden="true"
      >
        {/* Track */}
        <path
          d={arcPath(0, 100)}
          fill="none"
          stroke="#243040"
          strokeWidth={SW}
          strokeLinecap="round"
        />
        {/* Zone arcs */}
        {zones.map((z, i) => (
          <path
            key={i}
            d={z.path}
            fill="none"
            stroke={z.color}
            strokeWidth={SW}
            strokeLinecap="round"
          />
        ))}
        {/* Needle */}
        <line
          x1={CX}
          y1={CY + 7}
          x2={CX}
          y2={CY - R * 0.8}
          stroke="#dce6ef"
          strokeWidth="2.5"
          strokeLinecap="round"
          style={{
            transformOrigin: `${CX}px ${CY}px`,
            transform: `rotate(${needleRot}deg)`,
            transition: "transform 600ms cubic-bezier(0.22,1,0.36,1)",
          }}
        />
        {/* Hub */}
        <circle cx={CX} cy={CY} r="6" fill="#1c2530" stroke="#405060" strokeWidth="1.5" />
      </svg>

      <div
        className={clsx(
          "font-display text-2xl font-semibold tabular-nums leading-none",
          active ? "text-accent-cyan" : "text-text",
        )}
      >
        {statDisplay}
      </div>
    </>
  );
}
