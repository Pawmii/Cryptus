import { useMemo, useRef, useState } from "react";

export interface BarDatum { key: string; label: string; value: number; detail?: string }

/* Single-series bar chart. Thin bars with a 2px surface gap, rounded data-ends
   anchored to the baseline, recessive grid, per-bar hover/focus tooltip. */
export function BarChart({ data, height = 200, format, ariaLabel }: {
  data: BarDatum[]; height?: number; format: (v: number) => string; ariaLabel: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const W = 720;
  const padL = 44, padB = 22, padT = 8;
  const max = useMemo(() => {
    const m = Math.max(0, ...data.map((d) => d.value));
    if (m === 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(m)));
    return Math.ceil(m / p) * p;
  }, [data]);
  const innerH = height - padB - padT;
  const step = (W - padL) / Math.max(data.length, 1);
  const barW = Math.max(2, Math.min(18, step - 2));
  const ticks = [0, 0.5, 1].map((t) => t * max);
  const labelEvery = Math.ceil(data.length / 8);
  const d = hover !== null ? data[hover] : null;
  const tipX = hover !== null ? ((padL + hover * step + step / 2) / W) * 100 : 0;
  const tipY = d ? padT + innerH - (d.value / max) * innerH : 0;

  return (
    <div className="chart" ref={wrap} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={ariaLabel} preserveAspectRatio="none" style={{ height }}>
        <g className="chart__grid">
          {ticks.map((t) => {
            const y = padT + innerH - (t / max) * innerH;
            return <line key={t} x1={padL} x2={W} y1={y} y2={y} />;
          })}
        </g>
        <g className="chart__axis">
          {ticks.map((t) => (
            <text key={t} x={padL - 8} y={padT + innerH - (t / max) * innerH + 3} textAnchor="end">{format(t)}</text>
          ))}
          {data.map((b, i) => i % labelEvery === 0 && (
            <text key={b.key} x={padL + i * step + step / 2} y={height - 6} textAnchor="middle">{b.label}</text>
          ))}
        </g>
        {data.map((b, i) => {
          const h = b.value > 0 ? Math.max(2, (b.value / max) * innerH) : 0;
          const x = padL + i * step + (step - barW) / 2;
          const y = padT + innerH - h;
          const r = Math.min(4, barW / 2, h);
          const path = h === 0 ? "" :
            `M${x},${padT + innerH} V${y + r} Q${x},${y} ${x + r},${y} H${x + barW - r} Q${x + barW},${y} ${x + barW},${y + r} V${padT + innerH} Z`;
          return (
            <g key={b.key}>
              <rect className="chart__hit" x={padL + i * step} y={padT} width={step} height={innerH} tabIndex={0}
                aria-label={`${b.label}: ${format(b.value)}`}
                onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
              <path className="chart__bar" d={path} opacity={hover === null || hover === i ? 1 : 0.45} />
            </g>
          );
        })}
      </svg>
      {d && (
        <div className="chart__tip" style={{ left: `${tipX}%`, top: (tipY / height) * (wrap.current?.clientHeight ?? height) }}>
          <strong>{format(d.value)}</strong>
          <span>{d.label}{d.detail ? ` · ${d.detail}` : ""}</span>
        </div>
      )}
    </div>
  );
}
