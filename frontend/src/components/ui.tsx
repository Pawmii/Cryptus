import {
  createContext, useCallback, useContext, useEffect, useId, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "./Icon";

/* ── Button ─────────────────────────────────────────────────────────── */
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  icon?: IconName;
  iconRight?: IconName;
  loading?: boolean;
  block?: boolean;
};

export function Button({ variant = "secondary", size = "md", icon, iconRight, loading, block, className = "",
  children, type = "button", ...rest }: BtnProps) {
  const cls = ["btn", `btn--${variant}`, size !== "md" && `btn--${size}`, block && "btn--block",
    !children && icon && "btn--icon", className].filter(Boolean).join(" ");
  return (
    <button type={type} className={cls} data-loading={loading || undefined} aria-busy={loading || undefined}
      disabled={rest.disabled || loading} {...rest}>
      {icon && <Icon name={icon} size={size === "sm" ? 14 : 16} />}
      {children}
      {iconRight && <Icon name={iconRight} size={size === "sm" ? 14 : 16} />}
    </button>
  );
}

/* ── Form fields ────────────────────────────────────────────────────── */
export function Field({ label, hint, error, aside, children, htmlFor }: {
  label?: ReactNode; hint?: ReactNode; error?: string | null; aside?: ReactNode; children: ReactNode; htmlFor?: string;
}) {
  return (
    <div className="field">
      {(label || aside) && (
        <div className="field__label">
          {label && <label htmlFor={htmlFor}>{label}</label>}
          {aside && <span className="xs muted">{aside}</span>}
        </div>
      )}
      {children}
      {error ? <span className="field__error" role="alert"><Icon name="alert" size={13} />{error}</span>
        : hint ? <span className="field__hint">{hint}</span> : null}
    </div>
  );
}

type InputProps = InputHTMLAttributes<HTMLInputElement> & { label?: ReactNode; hint?: ReactNode; error?: string | null;
  mono?: boolean; suffix?: ReactNode; aside?: ReactNode };

export function Input({ label, hint, error, mono, suffix, aside, id, className = "", ...rest }: InputProps) {
  const auto = useId();
  const fid = id ?? auto;
  const input = <input id={fid} className={`input ${mono ? "input--mono" : ""} ${className}`}
    aria-invalid={error ? true : undefined} {...rest} />;
  return (
    <Field label={label} hint={hint} error={error} aside={aside} htmlFor={fid}>
      {suffix ? <div className="input-group">{input}<span className="input-group__suffix">{suffix}</span></div> : input}
    </Field>
  );
}

export function Select({ label, hint, error, id, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & {
  label?: ReactNode; hint?: ReactNode; error?: string | null }) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <Field label={label} hint={hint} error={error} htmlFor={fid}>
      <select id={fid} className="select" aria-invalid={error ? true : undefined} {...rest}>{children}</select>
    </Field>
  );
}

export function Textarea({ label, hint, error, id, mono, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label?: ReactNode; hint?: ReactNode; error?: string | null; mono?: boolean }) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <Field label={label} hint={hint} error={error} htmlFor={fid}>
      <textarea id={fid} className={`textarea ${mono ? "input--mono" : ""}`} aria-invalid={error ? true : undefined} {...rest} />
    </Field>
  );
}

export function Switch({ label, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return <label className="switch"><input type="checkbox" role="switch" {...rest} /><span>{label}</span></label>;
}

export function Check({ label, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return <label className="check"><input type="checkbox" {...rest} /><span>{label}</span></label>;
}

/* ── Surfaces ───────────────────────────────────────────────────────── */
export function Panel({ title, eyebrow, actions, children, footer, flush, raised, accent, className = "", id }: {
  title?: ReactNode; eyebrow?: ReactNode; actions?: ReactNode; children?: ReactNode; footer?: ReactNode;
  flush?: boolean; raised?: boolean; accent?: boolean; className?: string; id?: string;
}) {
  return (
    <section id={id} className={`panel ${raised ? "panel--raised" : ""} ${accent ? "panel--accent" : ""} ${className}`}>
      {(title || actions || eyebrow) && (
        <header className="panel__head">
          <div className="stack--sm" style={{ minWidth: 0 }}>
            {eyebrow && <span className="label">{eyebrow}</span>}
            {title && <h2>{title}</h2>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className={flush ? "panel__body--flush" : "panel__body"}>{children}</div>
      {footer && <footer className="panel__foot">{footer}</footer>}
    </section>
  );
}

export function Alert({ tone = "info", title, children, icon }: {
  tone?: "info" | "success" | "warning" | "danger" | "accent"; title?: ReactNode; children?: ReactNode; icon?: IconName;
}) {
  const ic: IconName = icon ?? (tone === "success" ? "check" : tone === "info" ? "info" : "alert");
  return (
    <div className={`alert alert--${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <Icon name={ic} size={16} />
      <div className="stack--sm">
        {title && <strong>{title}</strong>}
        {children && <div>{children}</div>}
      </div>
    </div>
  );
}

export function Empty({ icon = "asset", title, children, action }: {
  icon?: IconName; title: ReactNode; children?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty__mark"><Icon name={icon} /></span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Kv({ items }: { items: [ReactNode, ReactNode][] }) {
  return <dl className="kv">{items.map(([k, v], i) => <FragmentKv key={i} k={k} v={v} />)}</dl>;
}
function FragmentKv({ k, v }: { k: ReactNode; v: ReactNode }) {
  return <><dt>{k}</dt><dd>{v}</dd></>;
}

/* ── Status ─────────────────────────────────────────────────────────── */
const TONE: Record<string, string> = {
  paid: "success", confirmed: "success", succeeded: "success", operational: "success", active: "success",
  processing: "warning", underpaid: "warning", awaiting_approval: "warning", awaiting_external_signature: "warning",
  degraded: "warning", approved: "info", signing: "info", broadcast: "info", pending: "info",
  expired: "quiet", cancelled: "quiet", disabled: "quiet", planned: "quiet", not_configured: "quiet", revoked: "quiet",
  orphaned: "danger", rejected: "danger", failed: "danger", dead: "danger", halted: "danger",
  live: "accent", test: "info", planned_sweep: "info",
};
const LABEL: Record<string, string> = {
  awaiting_approval: "Awaiting approval", awaiting_external_signature: "Needs signature",
  not_configured: "Not configured",
};

export function Status({ value, live }: { value: string; live?: boolean }) {
  const tone = TONE[value] ?? "quiet";
  return <span className={`badge badge--${tone} ${live ? "badge--live" : ""}`}>{LABEL[value] ?? value.replace(/_/g, " ")}</span>;
}

/* ── Copy ───────────────────────────────────────────────────────────── */
export function useCopy() {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async (v: string) => {
    try {
      await navigator.clipboard.writeText(v);
    } catch {
      const t = document.createElement("textarea");
      t.value = v;
      document.body.appendChild(t);
      t.select();
      document.execCommand("copy");
      t.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }, []);
  return { copied, copy };
}

export function CopyField({ value, label }: { value: string; label?: string }) {
  const { copied, copy } = useCopy();
  return (
    <div className="copy">
      <div className="copy__value">{value}</div>
      <button type="button" className="copy__btn" data-copied={copied} onClick={() => copy(value)}
        aria-label={copied ? "Copied" : `Copy ${label ?? "value"}`} title={copied ? "Copied" : "Copy"}>
        <Icon name={copied ? "check" : "copy"} size={16} />
      </button>
    </div>
  );
}

export function CopyInline({ value, display }: { value: string; display?: ReactNode }) {
  const { copied, copy } = useCopy();
  return (
    <button type="button" className="btn btn--ghost btn--sm mono" style={{ paddingInline: 6 }} onClick={() => copy(value)}
      title="Copy" aria-label={`Copy ${value}`}>
      {display ?? value}
      <Icon name={copied ? "check" : "copy"} size={12} />
    </button>
  );
}

/* ── Segmented / Tabs ───────────────────────────────────────────────── */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, options, onChange }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {options.map((o) => (
        <button key={o.value} role="tab" type="button" aria-selected={o.value === value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

/* ── Dialog ─────────────────────────────────────────────────────────── */
export function Dialog({ open, onClose, title, description, children, footer, wide }: {
  open: boolean; onClose: () => void; title: ReactNode; description?: ReactNode; children?: ReactNode;
  footer?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const first = el?.querySelector<HTMLElement>("input, select, textarea, button:not([data-close])");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && el) {
        const f = Array.from(el.querySelectorAll<HTMLElement>("a, button, input, select, textarea, [tabindex]:not([tabindex='-1'])"))
          .filter((x) => !x.hasAttribute("disabled"));
        if (!f.length) return;
        const [a, z] = [f[0], f[f.length - 1]];
        if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
        else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      prev?.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className={`panel dialog ${wide ? "dialog--wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="dialog__head">
          <div className="row row--between row--nowrap">
            <h2 id={titleId} style={{ fontSize: "var(--text-lg)" }}>{title}</h2>
            <Button variant="ghost" size="sm" icon="x" onClick={onClose} aria-label="Close" data-close="true" />
          </div>
          {description && <p className="small muted">{description}</p>}
        </div>
        <div className="dialog__body">{children}</div>
        {footer && <div className="dialog__foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

/* ── Toasts ─────────────────────────────────────────────────────────── */
type Toast = { id: number; tone: "success" | "error" | "info"; text: ReactNode };
const ToastCtx = createContext<(tone: Toast["tone"], text: ReactNode) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: ReactNode) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x.slice(-3), { id, tone, text }]);
    window.setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "error" ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`panel toast toast--${t.tone}`}>
            <Icon name={t.tone === "success" ? "check" : t.tone === "error" ? "alert" : "info"} size={16} />
            <div className="grow">{t.text}</div>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/* ── Misc ───────────────────────────────────────────────────────────── */
export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function LoadingBlock() {
  return <div style={{ padding: "var(--s-10)", display: "grid", placeItems: "center" }}><Spinner /></div>;
}

export function ErrorBlock({ error, retry }: { error: { message: string } | null; retry?: () => void }) {
  if (!error) return null;
  return (
    <Alert tone="danger" title="Could not load">
      <div className="row">{error.message}{retry && <Button size="sm" variant="ghost" icon="refresh" onClick={retry}>Retry</Button>}</div>
    </Alert>
  );
}

export function Confirmations({ have, need }: { have: number; need: number }) {
  const n = Math.min(Math.max(need, 1), 24);
  const on = Math.min(have, n);
  return (
    <div className="stack--sm">
      <div className="track" data-done={have >= need} aria-hidden="true">
        {Array.from({ length: n }, (_, i) => <span key={i} data-on={i < on} />)}
      </div>
      <span className="xs muted num">{Math.min(have, need)} / {need} confirmations</span>
    </div>
  );
}

export function PageHead({ title, children, actions }: { title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}
