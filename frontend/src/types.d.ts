declare module "react-calendar-heatmap" {
  import { ComponentType, CSSProperties, ReactNode } from "react";

  export interface HeatmapValue {
    date: string | Date;
    [key: string]: unknown;
  }

  export interface CalendarHeatmapProps {
    values: HeatmapValue[];
    startDate: Date | string;
    endDate: Date | string;
    classForValue?: (value: HeatmapValue | undefined) => string;
    titleForValue?: (value: HeatmapValue | undefined) => string;
    tooltipDataAttrs?:
      | ((value: HeatmapValue | undefined) => Record<string, string>)
      | Record<string, string>;
    showMonthLabels?: boolean;
    showWeekdayLabels?: boolean;
    showOutOfRangeDays?: boolean;
    horizontal?: boolean;
    gutterSize?: number;
    onClick?: (value: HeatmapValue | undefined) => void;
    onMouseOver?: (event: unknown, value: HeatmapValue | undefined) => void;
    onMouseLeave?: (event: unknown, value: HeatmapValue | undefined) => void;
    transformDayElement?: (element: ReactNode, value: HeatmapValue | undefined, index: number) => ReactNode;
    style?: CSSProperties;
  }

  const CalendarHeatmap: ComponentType<CalendarHeatmapProps>;
  export default CalendarHeatmap;
}

declare module "react-calendar-heatmap/dist/styles.css";

declare module "*.svg" {
  const src: string;
  export default src;
}
