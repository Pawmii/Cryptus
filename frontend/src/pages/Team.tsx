import { Fragment, useState } from "react";
import { Alert, Button, Dialog, ErrorBlock, Input, LoadingBlock, PageHead, Panel, Select, Status, useToast } from "../components/ui";
import { api } from "../lib/api";
import { relative } from "../lib/format";
import { useApi } from "../lib/hooks";
import { useSession, type User } from "../lib/session";

const ROLE_DESC: Record<string, string> = {
  owner: "Everything, including team management and lifting the emergency stop.",
  admin: "Networks, wallets, consolidation approvals, webhooks, API keys, settings.",
  operator: "Create and cancel invoices, export reports. Read-only elsewhere.",
  viewer: "Read-only access to operations.",
};

export function Team() {
  const users = useApi<(User & { is_active: boolean })[]>("/api/admin/users");
  const { user: me, can } = useSession();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  if (!users.data) return users.error ? <ErrorBlock error={users.error} /> : <LoadingBlock />;

  async function patch(id: string, body: object) {
    try { await api.patch(`/api/admin/users/${id}`, body); users.reload(); toast("success", "Member updated"); }
    catch (e) { toast("error", (e as Error).message); }
  }

  return (
    <>
      <PageHead title="Team" actions={can("owner") && <Button size="sm" variant="primary" icon="plus" onClick={() => setAdding(true)}>Add member</Button>}>
        Least privilege by default. Every action a member takes is attributed to them in the audit log.
      </PageHead>
      <Panel flush>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Member</th><th>Role</th><th>2FA</th><th>Status</th><th className="r">Last sign-in</th><th /></tr></thead>
          <tbody>{users.data.map((u) => (
            <tr key={u.id}>
              <td><div className="vstack"><span>{u.name}{u.id === me?.id && <span className="tag" style={{ marginLeft: 6 }}>you</span>}</span>
                <span className="xs faint">{u.email}</span></div></td>
              <td>{can("owner") && u.id !== me?.id ? (
                <select className="select" style={{ height: 30, width: 130 }} value={u.role} onChange={(e) => patch(u.id, { role: e.target.value })}
                  aria-label={`Role for ${u.name}`}>{Object.keys(ROLE_DESC).map((r) => <option key={r}>{r}</option>)}</select>
              ) : <span className="small">{u.role}</span>}</td>
              <td>{u.totp_enabled ? <Status value="active" /> : <span className="xs" style={{ color: "var(--warning)" }}>off</span>}</td>
              <td><Status value={u.is_active ? "active" : "disabled"} /></td>
              <td className="r xs muted">{relative(u.last_login_at)}</td>
              <td className="r nowrap">{can("owner") && u.id !== me?.id && <>
                {u.totp_enabled && <Button size="sm" variant="ghost" onClick={() => patch(u.id, { reset_mfa: true })}>Reset 2FA</Button>}
                <Button size="sm" variant="ghost" onClick={() => patch(u.id, { is_active: !u.is_active })}>{u.is_active ? "Disable" : "Enable"}</Button>
              </>}</td>
            </tr>))}</tbody></table></div>
      </Panel>
      <Panel title="Roles">
        <dl className="kv">{Object.entries(ROLE_DESC).map(([r, d]) => <Fragment key={r}><dt className="mono">{r}</dt><dd>{d}</dd></Fragment>)}</dl>
      </Panel>
      {adding && <AddMember onClose={() => setAdding(false)} onDone={() => { setAdding(false); users.reload(); }} />}
    </>
  );
}

function AddMember({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: "", email: "", role: "operator", password: "" });
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog open onClose={onClose} title="Add team member" description="Share the temporary password through a secure channel; ask them to change it and enable 2FA."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.email || f.password.length < 12}
        onClick={async () => { try { await api.post("/api/admin/users", f); onDone(); } catch (e) { setError((e as Error).message); } }}>Add member</Button></>}>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="grid-2">
        <Input label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <Input label="Email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
        <Select label="Role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
          {["admin", "operator", "viewer"].map((r) => <option key={r}>{r}</option>)}
        </Select>
        <Input label="Temporary password" type="password" autoComplete="new-password" value={f.password}
          onChange={(e) => setF({ ...f, password: e.target.value })} hint="12+ characters" />
      </div>
    </Dialog>
  );
}
