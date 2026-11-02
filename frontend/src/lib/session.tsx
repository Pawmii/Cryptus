import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setCsrf } from "./api";

export interface User {
  id: string; email: string; name: string; role: "owner" | "admin" | "operator" | "viewer";
  totp_enabled: boolean; last_login_at: string | null; created_at: string;
}

interface Ctx {
  user: User | null;
  ready: boolean;
  mfaEnforced: boolean;
  setSession: (u: User | null, csrf?: string | null) => void;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  can: (role: User["role"]) => boolean;
}

const RANK = { viewer: 0, operator: 1, admin: 2, owner: 3 } as const;
const SessionCtx = createContext<Ctx>(null as unknown as Ctx);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [mfaEnforced, setMfaEnforced] = useState(false);

  const setSession = useCallback((u: User | null, csrf?: string | null) => {
    setUser(u);
    if (csrf !== undefined) setCsrf(csrf);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const r = await api.get<{ user: User; csrf_token: string; mfa_enforced: boolean }>("/api/auth/me");
      setCsrf(r.csrf_token);
      setUser(r.user);
      setMfaEnforced(r.mfa_enforced);
    } catch {
      setUser(null);
    } finally {
      setReady(true);
    }
  }, []);

  const logout = useCallback(async () => {
    try { await api.post("/api/auth/logout"); } catch { /* session already gone */ }
    setCsrf(null);
    setUser(null);
  }, []);

  useEffect(() => {
    refresh();
    const onUnauth = () => setUser(null);
    window.addEventListener("cryptus:unauthorized", onUnauth);
    return () => window.removeEventListener("cryptus:unauthorized", onUnauth);
  }, [refresh]);

  const can = useCallback((role: User["role"]) => !!user && RANK[user.role] >= RANK[role], [user]);

  return (
    <SessionCtx.Provider value={{ user, ready, mfaEnforced, setSession, refresh, logout, can }}>
      {children}
    </SessionCtx.Provider>
  );
}

export const useSession = () => useContext(SessionCtx);

export function applyTheme(theme?: string | null) {
  let t = theme;
  try {
    if (!t) t = localStorage.getItem("cryptus-theme");
    if (theme) localStorage.setItem("cryptus-theme", theme);
  } catch { /* storage unavailable */ }
  document.documentElement.dataset.theme = t === "light" ? "light" : "dark";
}
