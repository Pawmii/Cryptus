import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Icon, type IconName } from "../components/Icon";
import { Lockup } from "../components/Logo";
import { Alert, Button } from "../components/ui";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

type Item = { to: string; label: string; icon: IconName; role?: "admin" | "owner" };
const NAV: { group: string; items: Item[] }[] = [
  { group: "Operations", items: [
    { to: "/", label: "Overview", icon: "overview" },
    { to: "/invoices", label: "Invoices", icon: "invoice" },
    { to: "/payments", label: "Payments", icon: "payments" },
  ] },
  { group: "Treasury", items: [
    { to: "/consolidation", label: "Consolidation", icon: "sweep" },
    { to: "/wallets", label: "Wallets", icon: "wallet" },
  ] },
  { group: "Infrastructure", items: [
    { to: "/networks", label: "Networks", icon: "networks" },
    { to: "/assets", label: "Assets", icon: "asset" },
  ] },
  { group: "Developers", items: [
    { to: "/developers/api-keys", label: "API keys", icon: "key", role: "admin" },
    { to: "/developers/webhooks", label: "Webhooks", icon: "webhook" },
  ] },
  { group: "Governance", items: [
    { to: "/team", label: "Team", icon: "team", role: "admin" },
    { to: "/audit", label: "Audit log", icon: "audit", role: "admin" },
    { to: "/settings", label: "Settings", icon: "settings" },
  ] },
];

const TITLES: [RegExp, string, string][] = [
  [/^\/$/, "Overview", "Operations"], [/^\/invoices\/.+/, "Invoice", "Operations"], [/^\/invoices/, "Invoices", "Operations"],
  [/^\/payments/, "Payments", "Operations"], [/^\/consolidation/, "Consolidation", "Treasury"],
  [/^\/wallets/, "Wallets", "Treasury"], [/^\/networks/, "Networks", "Infrastructure"],
  [/^\/assets/, "Assets", "Infrastructure"], [/^\/developers\/api/, "API keys", "Developers"],
  [/^\/developers\/web/, "Webhooks", "Developers"], [/^\/team/, "Team", "Governance"],
  [/^\/audit/, "Audit log", "Governance"], [/^\/settings/, "Settings", "Governance"], [/^\/account/, "Account", "Personal"],
];

export function ConsoleLayout() {
  const { user, logout, can, mfaEnforced } = useSession();
  const loc = useLocation();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const counts = useApi<{ kpis: { needs_review: number; sweeps_pending: number }; treasury: { sweeps_halted: boolean } }>(
    "/api/admin/overview?days=7", 30000);
  useEffect(() => setOpen(false), [loc.pathname]);
  if (!user) return null;
  const [, title, group] = TITLES.find(([r]) => r.test(loc.pathname)) ?? [null, "CRYPTUS", ""];
  const badge = (to: string) => {
    const k = counts.data?.kpis;
    if (!k) return null;
    if (to === "/invoices" && k.needs_review) return k.needs_review;
    if (to === "/consolidation" && k.sweeps_pending) return k.sweeps_pending;
    return null;
  };
  const initials = user.name.split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();

  return (
    <div className="shell" data-nav={open ? "open" : "closed"}>
      {open && <div className="nav-scrim" onClick={() => setOpen(false)} />}
      <aside className="sidebar" aria-label="Primary">
        <div className="sidebar__brand"><Lockup size={26} /></div>
        <nav className="sidebar__nav">
          {NAV.map((g) => {
            const items = g.items.filter((i) => !i.role || can(i.role));
            if (!items.length) return null;
            return (
              <div className="nav-group" key={g.group}>
                <span className="label">{g.group}</span>
                {items.map((i) => (
                  <NavLink key={i.to} to={i.to} end={i.to === "/"} className="nav-link">
                    <Icon name={i.icon} size={17} />
                    {i.label}
                    {badge(i.to) ? <span className="count">{badge(i.to)}</span> : null}
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <div className="sidebar__foot">
          <NavLink to="/account" className="nav-link" style={{ height: "auto", padding: 0 }}>
            <span className="user-chip">
              <span className="user-chip__avatar">{initials}</span>
              <span className="vstack" style={{ minWidth: 0 }}>
                <span className="small truncate" style={{ color: "var(--fg)" }}>{user.name}</span>
                <span className="xs muted truncate">{user.role} · {user.email}</span>
              </span>
            </span>
          </NavLink>
          <Button variant="ghost" size="sm" icon="logout" block onClick={async () => { await logout(); nav("/login"); }}>
            Sign out
          </Button>
        </div>
      </aside>
      <div className="main">
        {counts.data?.treasury.sweeps_halted && (
          <div className="halt-banner" role="alert">
            <Icon name="halt" size={16} />
            Treasury emergency stop is active — no consolidation will be planned, signed or broadcast.
          </div>
        )}
        <header className="topbar">
          <Button variant="ghost" size="sm" icon="menu" className="menu-btn" aria-label="Open navigation" onClick={() => setOpen(true)} />
          <nav className="topbar__crumb" aria-label="Breadcrumb">
            <span className="label">{group}</span>
            <span className="topbar__sep" aria-hidden="true">/</span>
            <span className="topbar__here" aria-current="page">{title}</span>
          </nav>
          <div className="topbar__actions">
            {can("operator") && loc.pathname !== "/invoices" && (
              <Button variant="secondary" size="sm" icon="plus" onClick={() => nav("/invoices?new=1")}>New invoice</Button>
            )}
          </div>
        </header>
        {mfaEnforced && (
          <div style={{ padding: "var(--s-4) var(--s-8) 0" }}>
            <Alert tone="warning" title="Two-factor authentication is required for your role">
              Enable it on your <NavLink to="/account">account page</NavLink> to keep administrative access.
            </Alert>
          </div>
        )}
        <main id="main" className="page" key={loc.pathname}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
