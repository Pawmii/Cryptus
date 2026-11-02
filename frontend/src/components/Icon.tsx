/* CRYPTUS icon set — 20px grid, 1.5px stroke, square caps, mitred joins.
   Sharp geometry only: every icon is built from straight segments and 45° cuts. */
import type { SVGProps } from "react";

const P: Record<string, string> = {
  overview: "M3 3h6v6H3zM11 3h6v4h-6zM11 9h6v8h-6zM3 11h6v6H3z",
  invoice: "M5 2.5h7l3.5 3.5v11.5h-10.5zM12 2.5V6h3.5M7.5 10h5M7.5 13h5",
  payments: "M3 7h11l-3-3M17 13H6l3 3",
  networks: "M10 2.5l6.5 3.75v7.5L10 17.5l-6.5-3.75v-7.5zM10 2.5V10m0 0l6.5 3.75M10 10l-6.5 3.75",
  wallet: "M2.5 5.5h13v11h-13zM2.5 5.5L13 2.5v3M12 11h3.5",
  asset: "M10 2.5l7.5 7.5-7.5 7.5L2.5 10zM10 6.5L13.5 10 10 13.5 6.5 10z",
  sweep: "M3 4h4l3 6 3-6h4M10 10v7M7 14l3 3 3-3",
  webhook: "M7 6.5a3 3 0 1 1 3.5 3L13 14M5.5 17a3 3 0 0 1-.5-5.9M14.5 17a3 3 0 1 0-3-3H7",
  key: "M12.5 2.5a5 5 0 1 1-4.6 7L2.5 15v2.5H5v-2h2v-2h2l.9-.9M13.5 6.5h.01",
  team: "M7.5 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2.5 17v-1.5a4 4 0 0 1 4-4h2a4 4 0 0 1 4 4V17M13 3.2a3 3 0 0 1 0 5.6M15 11.6a4 4 0 0 1 2.5 3.9V17",
  audit: "M10 2.5l6.5 2.5v5c0 3.6-2.8 6.4-6.5 7.5C6.3 16.4 3.5 13.6 3.5 10V5zM7 10l2 2 4-4",
  settings: "M3 5h8M14 5h3M3 10h3M9 10h8M3 15h9M15 15h2M11 3.5v3M6 8.5v3M12 13.5v3",
  logout: "M8 3H3.5v14H8M12 6l4 4-4 4M16 10H7",
  copy: "M7 7h10v10H7zM13 7V3H3v10h4",
  check: "M4 10.5l4 4 8-9",
  x: "M5 5l10 10M15 5L5 15",
  alert: "M10 2.5l8 14.5H2zM10 8v4M10 14.5v.5",
  info: "M10 2.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15zM10 9v5M10 6v.5",
  external: "M11 3h6v6M17 3l-8 8M14 11v6H3V6h6",
  plus: "M10 4v12M4 10h12",
  refresh: "M16 4v4h-4M4 16v-4h4M15.5 8A6 6 0 0 0 5 6.5M4.5 12A6 6 0 0 0 15 13.5",
  chevron: "M8 5l5 5-5 5",
  chevronDown: "M5 8l5 5 5-5",
  search: "M8.5 3a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM12.5 12.5L17 17",
  lock: "M4.5 9h11v8.5h-11zM7 9V6a3 3 0 0 1 6 0v3M10 12.5v2",
  halt: "M7 2.5h6l4.5 4.5v6L13 17.5H7L2.5 13V7zM7 10h6",
  clock: "M10 2.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15zM10 6v4l3 2",
  arrowRight: "M3.5 10h13M12 5.5l4.5 4.5-4.5 4.5",
  arrowLeft: "M16.5 10h-13M8 5.5L3.5 10 8 14.5",
  eye: "M2 10s3-5.5 8-5.5S18 10 18 10s-3 5.5-8 5.5S2 10 2 10zM10 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  download: "M10 3v10M5.5 8.5L10 13l4.5-4.5M3 17h14",
  menu: "M3 5h14M3 10h14M3 15h14",
  signer: "M3 3h14v9H3zM6 17h8M10 12v5M6.5 7.5h.01M10 7.5h4",
  pulse: "M2 10h4l2-5 4 10 2-5h4",
  shield: "M10 2.5l6.5 2.5v5c0 3.6-2.8 6.4-6.5 7.5C6.3 16.4 3.5 13.6 3.5 10V5z",
  sun: "M10 6.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M4 4l1.4 1.4M14.6 14.6L16 16M4 16l1.4-1.4M14.6 5.4L16 4",
  moon: "M15.5 12.5A6.5 6.5 0 0 1 7.5 4.5a6.5 6.5 0 1 0 8 8z",
  trash: "M3.5 5.5h13M8 5.5V3h4v2.5M5 5.5l1 11.5h8l1-11.5",
};

export type IconName = keyof typeof P;

export function Icon({ name, size = 18, ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5}
      strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" focusable="false" {...rest}>
      <path d={P[name]} />
    </svg>
  );
}
