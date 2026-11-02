/* The CRYPTUS seal: an octagonal vault door left open on one side (the "C"),
   a crimson diamond core, and the bolt that slides out through the opening. */

export function Mark({ size = 28, title = "CRYPTUS" }: { size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label={title}>
      <path d="M29 13.5V11.2L20.8 3H11.2L3 11.2v9.6l8.2 8.2h9.6l8.2-8.2v-2.3" fill="none" stroke="var(--fg)"
        strokeWidth="2.4" strokeLinecap="square" />
      <path d="M16 11.2 20.8 16 16 20.8 11.2 16Z" fill="var(--accent)" />
      <path d="M20.8 14.8h7.6v2.4h-7.6z" fill="var(--accent)" />
    </svg>
  );
}

export function Wordmark({ size = 15 }: { size?: number }) {
  return (
    <span className="wordmark" style={{ fontSize: size }}>CRYPTUS</span>
  );
}

export function Lockup({ size = 26, compact = false }: { size?: number; compact?: boolean }) {
  return (
    <span className="lockup">
      <Mark size={size} />
      {!compact && <Wordmark size={Math.round(size * 0.55)} />}
    </span>
  );
}
