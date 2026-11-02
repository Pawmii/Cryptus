import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Mark } from "../components/Logo";
import { Alert, Button, Input } from "../components/ui";
import { AuthArt } from "../layouts/AuthArt";
import { api, setCsrf } from "../lib/api";
import { useSession, type User } from "../lib/session";

/** Only same-origin app paths: no "//host", no backslashes, no whitespace/control characters, no schemes. */
export function safeNext(next: string | null): string {
  if (!next || next[0] !== "/" || next[1] === "/") return "/";
  for (let i = 0; i < next.length; i++) {
    const c = next.charCodeAt(i);
    if (c === 0x5c || c <= 0x20 || c === 0x7f) return "/";
  }
  try {
    const u = new URL(next, window.location.origin);
    return u.origin === window.location.origin ? u.pathname + u.search + u.hash : "/";
  } catch {
    return "/";
  }
}

export function Login() {
  const { setSession } = useSession();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [stage, setStage] = useState<"password" | "mfa">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const next = params.get("next");
  const dest = safeNext(next);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      if (stage === "password") {
        const r = await api.post<{ mfa_required: boolean; csrf_token: string; user: User | null }>("/api/auth/login",
          { email, password });
        setCsrf(r.csrf_token);
        if (r.mfa_required) {
          setStage("mfa");
          setPassword("");
        } else {
          setSession(r.user, r.csrf_token);
          nav(dest, { replace: true });
        }
      } else {
        const r = await api.post<{ user: User; csrf_token: string }>("/api/auth/mfa", { code });
        setSession(r.user, r.csrf_token);
        nav(dest, { replace: true });
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="auth">
      <AuthArt />
      <div className="auth__form">
        <form className="auth__card" onSubmit={submit} noValidate>
          <div className="stack">
            <Mark size={36} />
            <div className="stack--sm">
              <span className="label">{stage === "password" ? "Operator console" : "Second factor"}</span>
              <h1>{stage === "password" ? "Sign in to CRYPTUS" : "Verify it's you"}</h1>
            </div>
            <div className="seam" />
          </div>
          {error && <Alert tone="danger">{error}</Alert>}
          {stage === "password" ? (
            <>
              <Input label="Email" type="email" autoComplete="username" required value={email}
                onChange={(e) => setEmail(e.target.value)} autoFocus />
              <Input label="Password" type="password" autoComplete="current-password" required value={password}
                onChange={(e) => setPassword(e.target.value)} />
            </>
          ) : (
            <Input label="Authentication code" hint="Six digits from your authenticator app." inputMode="numeric"
              autoComplete="one-time-code" mono maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
          )}
          <Button type="submit" variant="primary" size="lg" block loading={pending} iconRight="arrowRight"
            disabled={stage === "password" ? !email || !password : code.length < 6}>
            {stage === "password" ? "Continue" : "Verify"}
          </Button>
          <p className="xs faint">
            Sessions are bound to this browser, expire automatically, and every sign-in is recorded in the tamper-evident
            audit log. Lost access? An operator with shell access can run <code>cryptus reset-password</code>.
          </p>
        </form>
      </div>
    </div>
  );
}
