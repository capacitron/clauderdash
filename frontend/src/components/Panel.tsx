import clsx from "clsx";
import { ReactNode } from "react";

type Props = {
  kicker: string;
  title: string;
  titleIcon?: ReactNode;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  centerpiece?: boolean;
};

export function Panel({ kicker, title, titleIcon, subtitle, action, children, className, centerpiece }: Props) {
  return (
    <section
      className={clsx(
        "panel flex flex-col break-inside-avoid mb-5",
        centerpiece && "border-accent-purple/40 bg-gradient-to-br from-accent-purple/10 via-surface to-accent-pink/5",
        className,
      )}
    >
      <header className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className={clsx("kicker mb-1", centerpiece && "text-accent-purple")}>{kicker}</div>
          <h2 className="flex items-center gap-1.5 text-base font-semibold text-text">
            {titleIcon && <span className="flex-shrink-0">{titleIcon}</span>}
            {title}
          </h2>
          {subtitle && <p className="mt-0.5 text-xs text-text-dim">{subtitle}</p>}
        </div>
        {action && <div className="flex-shrink-0">{action}</div>}
      </header>
      <div className="flex flex-1 flex-col">{children}</div>
    </section>
  );
}
