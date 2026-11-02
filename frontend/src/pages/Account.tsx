import { useState } from "react";
import { Alert, Button, Input, Kv, PageHead, Panel, Segmented, useToast } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, relative } from "../lib/format";
import { useApi } from "../lib/hooks";
import { applyTheme, useSession } from "../lib/session";

export function Account() {
  const { user, refresh } = useSession();
  const toast = useToast();
  const sessions = useApi<{ id: string; current: boolean; ip: string; user_agent: string; last_seen_at: string }[]>("/api/auth/sessions");
  const [pw, setPw] = useState({ current: "", next: "" });
  const [totp, setTotp] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">((document.documentElement.dataset.theme as "dark" | "light") ?? "dark");
  if (!user) return null;

  return (
    <>
      <PageHead title="Account">Your personal security settings.</PageHead>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="grid-2" style={{ alignItems: "start" }}>
        <Panel title="Profile">
          <Kv items={[["Name", user.name], ["Email", user.email], ["Role", user.role], ["Member since", dateTime(user.created_at)]]} />
          <div className="row" style={{ marginTop: "var(--s-4)" }}>
            <span className="small muted">Appearance</span>
            <Segmented label="Theme" value={theme} onChange={(t) => { setTheme(t); applyTheme(t); }}
              options={[{ value: "dark", label: "Vault (dark)" }, { value: "light", label: "Ledger (light)" }]} />
          </div>
        </Panel>
        <Panel title="Two-factor authentication" eyebrow={user.totp_enabled ? "Enabled" : "Not enabled"}>
          {user.totp_enabled ? (
            <div className="stack">
              <p className="small muted">Enter a current code to disable.</p>
              <Input label="Code" mono value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" />
              <div><Button variant="danger" onClick={async () => {
                try { await api.post("/api/auth/totp/disable", { code }); setCode(""); refresh(); toast("success", "2FA disabled"); }
                catch (e) { setError((e as Error).message); }
              }}>Disable 2FA</Button></div>
            </div>
          ) : totp ? (
            <div className="stack">
              <div className="row" style={{ alignItems: "flex-start" }}>
                <div className="qr-plate" style={{ width: 160, height: 160 }} dangerouslySetInnerHTML={{ __html: totp.qr_svg }} />
                <div className="grow stack--sm">
                  <span className="label">Manual key</span><span className="mono small break">{totp.secret}</span>
                </div>
              </div>
              <Input label="Code from your app" mono value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" />
              <div><Button variant="primary" onClick={async () => {
                try { await api.post("/api/auth/totp/confirm", { code }); setTotp(null); setCode(""); refresh(); toast("success", "2FA enabled"); }
                catch (e) { setError((e as Error).message); }
              }}>Confirm</Button></div>
            </div>
          ) : (
            <div className="stack"><p className="small muted">Protect sign-in with a time-based code (TOTP) from any authenticator app.</p>
              <div><Button variant="primary" icon="lock" onClick={async () => setTotp(await api.post("/api/auth/totp/begin"))}>Set up 2FA</Button></div></div>
          )}
        </Panel>
        <Panel title="Change password">
          <div className="stack">
            <Input label="Current password" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
            <Input label="New password" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })}
              hint="12+ characters. Other sessions are signed out." />
            <div><Button disabled={!pw.current || pw.next.length < 12} onClick={async () => {
              try { await api.post("/api/auth/password", { current_password: pw.current, new_password: pw.next }); setPw({ current: "", next: "" }); toast("success", "Password changed"); sessions.reload(); }
              catch (e) { setError((e as Error).message); }
            }}>Update password</Button></div>
          </div>
        </Panel>
        <Panel title="Active sessions" flush actions={<Button size="sm" variant="ghost" onClick={async () => { await api.post("/api/auth/sessions/revoke-others"); sessions.reload(); }}>Sign out others</Button>}>
          <table className="table"><tbody>{(sessions.data ?? []).map((s) => (
            <tr key={s.id}><td><div className="vstack"><span className="small truncate" style={{ maxWidth: 280 }}>{s.user_agent || "Unknown client"}</span>
              <span className="xs faint mono">{s.ip}</span></div></td>
              <td className="r xs muted">{s.current ? <span className="tag">this device</span> : relative(s.last_seen_at)}</td></tr>))}</tbody></table>
        </Panel>
      </div>
    </>
  );
}
