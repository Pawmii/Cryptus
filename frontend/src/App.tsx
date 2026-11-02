import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ToastProvider, LoadingBlock } from "./components/ui";
import { ConsoleLayout } from "./layouts/ConsoleLayout";
import { api } from "./lib/api";
import { SessionProvider, useSession } from "./lib/session";
import { Checkout } from "./pages/Checkout";
import { Login } from "./pages/Login";
import { Setup } from "./pages/Setup";
import { Overview } from "./pages/Overview";
import { Invoices } from "./pages/Invoices";
import { InvoiceDetail } from "./pages/InvoiceDetail";
import { Payments } from "./pages/Payments";
import { Networks } from "./pages/Networks";
import { Wallets } from "./pages/Wallets";
import { Assets } from "./pages/Assets";
import { Sweeps } from "./pages/Sweeps";
import { Webhooks } from "./pages/Webhooks";
import { ApiKeys } from "./pages/ApiKeys";
import { Team } from "./pages/Team";
import { Audit } from "./pages/Audit";
import { Settings } from "./pages/Settings";
import { Account } from "./pages/Account";

function Gate() {
  const { user, ready } = useSession();
  const loc = useLocation();
  const [hasOwner, setHasOwner] = useState<boolean | null>(null);

  useEffect(() => {
    api.get<{ has_owner: boolean }>("/api/setup/status").then((s) => setHasOwner(s.has_owner)).catch(() => setHasOwner(true));
  }, [user]);

  if (!ready || hasOwner === null) return <LoadingBlock />;
  if (!hasOwner && !loc.pathname.startsWith("/setup")) return <Navigate to="/setup" replace />;
  if (!user && !loc.pathname.startsWith("/setup") && loc.pathname !== "/login")
    return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname)}`} replace />;

  return (
    <Routes>
      <Route path="/setup/*" element={<Setup hasOwner={hasOwner} />} />
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route element={<ConsoleLayout />}>
        <Route index element={<Overview />} />
        <Route path="invoices" element={<Invoices />} />
        <Route path="invoices/:id" element={<InvoiceDetail />} />
        <Route path="payments" element={<Payments />} />
        <Route path="consolidation" element={<Sweeps />} />
        <Route path="wallets" element={<Wallets />} />
        <Route path="networks" element={<Networks />} />
        <Route path="assets" element={<Assets />} />
        <Route path="developers/api-keys" element={<ApiKeys />} />
        <Route path="developers/webhooks" element={<Webhooks />} />
        <Route path="team" element={<Team />} />
        <Route path="audit" element={<Audit />} />
        <Route path="settings" element={<Settings />} />
        <Route path="account" element={<Account />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <Routes>
          {/* The checkout is fully public and independent of the console session. */}
          <Route path="/pay/:id" element={<Checkout />} />
          <Route path="*" element={<SessionProvider><Gate /></SessionProvider>} />
        </Routes>
      </ToastProvider>
    </BrowserRouter>
  );
}
