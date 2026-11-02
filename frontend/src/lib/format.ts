const FIAT_SYMBOL: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", JPY: "¥", CHF: "CHF ", CNY: "¥", INR: "₹" };

export function isFiat(code: string) {
  return /^[A-Z]{3}$/.test(code);
}

/** Format a decimal string without float conversion (keeps exact digits). */
export function groupDigits(v: string): string {
  const neg = v.startsWith("-");
  const [i, f] = (neg ? v.slice(1) : v).split(".");
  const g = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (neg ? "-" : "") + g + (f ? "." + f : "");
}

export function money(amount: string, currency: string): string {
  if (isFiat(currency)) {
    const [i, f = ""] = amount.split(".");
    const cents = (f + "00").slice(0, 2);
    return `${FIAT_SYMBOL[currency] ?? currency + " "}${groupDigits(i)}.${cents}`;
  }
  return groupDigits(amount);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  const abs = Math.abs(s);
  const fmt = (n: number, u: string) => (s >= 0 ? `${n}${u} ago` : `in ${n}${u}`);
  if (abs < 45) return s >= 0 ? "just now" : "in a moment";
  if (abs < 3600) return fmt(Math.round(abs / 60), "m");
  if (abs < 86400) return fmt(Math.round(abs / 3600), "h");
  return fmt(Math.round(abs / 86400), "d");
}

export function shortHash(v: string | null | undefined, head = 8, tail = 6): string {
  if (!v) return "—";
  return v.length <= head + tail + 1 ? v : `${v.slice(0, head)}…${v.slice(-tail)}`;
}

export function countdown(ms: number): string {
  if (ms <= 0) return "00:00";
  const t = Math.floor(ms / 1000);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  const p = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}

export function compact(v: string | number): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function assetGlyph(symbol: string): string {
  return symbol.replace(/^t/, "").slice(0, 4).toUpperCase();
}
