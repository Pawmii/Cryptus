import { useState } from "react";
import { Alert, Button, Check, CopyField, Dialog, Empty, ErrorBlock, Input, LoadingBlock, PageHead, Panel, Segmented,
  Status, useToast } from "../components/ui";
import { api } from "../lib/api";
import { relative } from "../lib/format";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

interface Endpoint { id: string; url: string; description: string | null; events: string[]; mode: string; active: boolean;
  created_at: string; stats: Record<string, number> }
interface Delivery { id: string; event_type: string; status: string; attempts: number; last_status_code: number | null;
  last_error: string | null; created_at: string; next_attempt_at: string; payload: unknown }

const VERIFY_SNIPPET = `import hmac, hashlib, time

def verify(secret: str, raw_body: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:
        return False
    mac = hmac.new(secret.encode(), f"{parts['t']}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, parts["v1"])`;

export function Webhooks() {
  const list = useApi<{ items: Endpoint[]; event_types: string[] }>("/api/admin/webhooks");
  const { can } = useSession();
  const toast = useToast();
  const [editing, setEditing] = useState<Endpoint | "new" | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [logFor, setLogFor] = useState<Endpoint | null>(null);
  if (!list.data) return list.error ? <ErrorBlock error={list.error} /> : <LoadingBlock />;

  async function test(e: Endpoint) {
    try {
      const r = await api.post<{ status: string; status_code: number | null; error: string | null }>(`/api/admin/webhooks/${e.id}/test`);
      toast(r.status === "succeeded" ? "success" : "error", r.status === "succeeded" ? `Delivered (HTTP ${r.status_code})` : `Failed: ${r.error}`);
      list.reload();
    } catch (err) { toast("error", (err as Error).message); }
  }

  return (
    <>
      <PageHead title="Webhooks" actions={can("admin") && <Button size="sm" variant="primary" icon="plus" onClick={() => setEditing("new")}>Add endpoint</Button>}>
        Signed, at-least-once event delivery with exponential back-off for ~2 days. De-duplicate on the event <code>id</code>.
      </PageHead>
      <Panel flush>
        {list.data.items.length === 0 ? <Empty icon="webhook" title="No endpoints">Add an HTTPS endpoint to receive invoice and payment events.</Empty> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Endpoint</th><th>Mode</th><th>Events</th><th>Deliveries</th><th>State</th><th /></tr></thead>
            <tbody>{list.data.items.map((e) => (
              <tr key={e.id}>
                <td><div className="vstack"><span className="mono small break">{e.url}</span>{e.description && <span className="xs faint">{e.description}</span>}</div></td>
                <td><Status value={e.mode} /></td>
                <td className="xs muted">{e.events.length ? `${e.events.length} selected` : "all events"}</td>
                <td className="xs num"><span style={{ color: "var(--success)" }}>{e.stats.succeeded ?? 0} ok</span> · {e.stats.pending ?? 0} pending ·{" "}
                  <span style={{ color: (e.stats.dead ?? 0) ? "var(--danger)" : undefined }}>{e.stats.dead ?? 0} dead</span></td>
                <td><Status value={e.active ? "active" : "disabled"} /></td>
                <td className="r nowrap">
                  <Button size="sm" variant="ghost" onClick={() => setLogFor(e)}>Log</Button>
                  {can("admin") && <><Button size="sm" variant="ghost" onClick={() => test(e)}>Test</Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(e)}>Edit</Button></>}
                </td>
              </tr>))}</tbody></table></div>
        )}
      </Panel>
      <Panel title="Verifying signatures" eyebrow="Cryptus-Signature header">
        <pre className="code">{VERIFY_SNIPPET}</pre>
      </Panel>

      {editing && <EndpointDialog ep={editing === "new" ? null : editing} types={list.data.event_types}
        onClose={() => setEditing(null)} onSaved={(s) => { setEditing(null); if (s) setSecret(s); list.reload(); }} />}
      <Dialog open={!!secret} onClose={() => setSecret(null)} title="Signing secret"
        description="Store it in your application's secret manager. It will not be shown again."
        footer={<Button variant="primary" onClick={() => setSecret(null)}>I've stored it</Button>}>
        {secret && <CopyField value={secret} label="secret" />}
      </Dialog>
      {logFor && <DeliveryLog ep={logFor} onClose={() => setLogFor(null)} />}
    </>
  );
}

function EndpointDialog({ ep, types, onClose, onSaved }: { ep: Endpoint | null; types: string[]; onClose: () => void; onSaved: (secret?: string) => void }) {
  const [url, setUrl] = useState(ep?.url ?? "");
  const [description, setDescription] = useState(ep?.description ?? "");
  const [mode, setMode] = useState<"live" | "test">((ep?.mode as "live" | "test") ?? "live");
  const [events, setEvents] = useState<string[]>(ep?.events ?? []);
  const [active, setActive] = useState(ep?.active ?? true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  async function save() {
    setPending(true);
    setError(null);
    try {
      const body = { url, description: description || null, mode, events, active };
      if (ep) { await api.put(`/api/admin/webhooks/${ep.id}`, body); onSaved(); }
      else { const r = await api.post<{ secret: string }>("/api/admin/webhooks", body); onSaved(r.secret); }
    } catch (e) { setError((e as Error).message); } finally { setPending(false); }
  }
  async function rotate() {
    if (!ep) return;
    const r = await api.post<{ secret: string }>(`/api/admin/webhooks/${ep.id}/rotate-secret`);
    onSaved(r.secret);
  }
  async function remove() {
    if (!ep) return;
    await api.del(`/api/admin/webhooks/${ep.id}`);
    onSaved();
  }
  return (
    <Dialog wide open onClose={onClose} title={ep ? "Edit endpoint" : "Add endpoint"}
      footer={<>{ep && <><Button variant="danger" icon="trash" onClick={remove}>Delete</Button><Button variant="ghost" onClick={rotate}>Rotate secret</Button></>}
        <span className="grow" /><Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={save} loading={pending} disabled={!url}>Save</Button></>}>
      {error && <Alert tone="danger">{error}</Alert>}
      <Input label="URL" mono value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://shop.example.com/cryptus/webhook"
        hint="Must be HTTPS and publicly routable. Redirects are not followed." />
      <div className="grid-2">
        <Input label="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
        <div className="field"><span className="field__label"><label>Mode</label></span>
          <Segmented label="Mode" value={mode} onChange={setMode} options={[{ value: "live", label: "Live" }, { value: "test", label: "Test" }]} /></div>
      </div>
      <div className="stack--sm">
        <span className="label">Events (none selected = all)</span>
        <div className="grid-3">{types.map((t) => (
          <Check key={t} label={<span className="mono xs">{t}</span>} checked={events.includes(t)}
            onChange={(e) => setEvents(e.target.checked ? [...events, t] : events.filter((x) => x !== t))} />))}</div>
      </div>
      <Check label="Active" checked={active} onChange={(e) => setActive(e.target.checked)} />
    </Dialog>
  );
}

function DeliveryLog({ ep, onClose }: { ep: Endpoint; onClose: () => void }) {
  const log = useApi<Delivery[]>(`/api/admin/webhooks/${ep.id}/deliveries`, 10000);
  const [sel, setSel] = useState<Delivery | null>(null);
  const toast = useToast();
  return (
    <Dialog wide open onClose={onClose} title="Delivery log" description={ep.url}>
      {!log.data ? <LoadingBlock /> : log.data.length === 0 ? <p className="muted small">No deliveries yet.</p> : (
        <div className="table-wrap" style={{ maxHeight: 320 }}><table className="table">
          <thead><tr><th>Event</th><th>Status</th><th className="r">Attempts</th><th>Response</th><th className="r">Created</th><th /></tr></thead>
          <tbody>{log.data.map((d) => (
            <tr key={d.id} data-href onClick={() => setSel(d)}>
              <td className="mono xs">{d.event_type}</td><td><Status value={d.status} /></td>
              <td className="r num">{d.attempts}</td>
              <td className="xs muted truncate" style={{ maxWidth: 220 }}>{d.last_status_code ? `HTTP ${d.last_status_code}` : d.last_error ?? ""}</td>
              <td className="r xs muted nowrap">{relative(d.created_at)}</td>
              <td className="r">{["dead", "failed"].includes(d.status) &&
                <Button size="sm" variant="ghost" icon="refresh" onClick={async (e) => {
                  e.stopPropagation();
                  await api.post(`/api/admin/webhooks/deliveries/${d.id}/retry`);
                  toast("success", "Queued for redelivery");
                  log.reload();
                }}>Retry</Button>}</td>
            </tr>))}</tbody></table></div>
      )}
      {sel && <pre className="code" style={{ maxHeight: 260 }}>{JSON.stringify(sel.payload, null, 2)}</pre>}
    </Dialog>
  );
}
