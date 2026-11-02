import { useState } from "react";
import { Alert, Button, Check, CopyField, Dialog, Empty, ErrorBlock, Input, LoadingBlock, PageHead, Panel, Segmented, Status,
  useToast } from "../components/ui";
import { api } from "../lib/api";
import { relative } from "../lib/format";
import { useApi } from "../lib/hooks";

interface Key { id: string; name: string; prefix: string; scopes: string[]; mode: string; created_at: string;
  last_used_at: string | null; revoked_at: string | null }

const EXAMPLE = (base: string) => `curl ${base}/api/v1/invoices \\
  -H "Authorization: Bearer $CRYPTUS_KEY" \\
  -H "Idempotency-Key: order-1042" \\
  -H "Content-Type: application/json" \\
  -d '{"amount": "25.00", "currency": "USD", "order_id": "1042",
       "redirect_url": "https://shop.example.com/thanks"}'`;

export function ApiKeys() {
  const keys = useApi<{ items: Key[]; scopes: string[] }>("/api/admin/api-keys");
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [revoke, setRevoke] = useState<Key | null>(null);
  if (!keys.data) return keys.error ? <ErrorBlock error={keys.error} /> : <LoadingBlock />;
  return (
    <>
      <PageHead title="API keys" actions={<Button size="sm" variant="primary" icon="plus" onClick={() => setCreating(true)}>Create key</Button>}>
        Keys authenticate your store against the merchant API. Test keys can only create testnet invoices. Keys are stored
        as keyed hashes — CRYPTUS cannot show a key again after creation.
      </PageHead>
      <Panel flush>
        {keys.data.items.length === 0 ? <Empty icon="key" title="No API keys" /> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Name</th><th>Key</th><th>Mode</th><th>Scopes</th><th className="r">Last used</th><th /></tr></thead>
            <tbody>{keys.data.items.map((k) => (
              <tr key={k.id} style={{ opacity: k.revoked_at ? 0.5 : 1 }}>
                <td>{k.name}</td><td className="mono xs">{k.prefix}_••••</td><td><Status value={k.mode} /></td>
                <td className="xs mono muted">{k.scopes.join(" ")}</td>
                <td className="r xs muted">{k.revoked_at ? "revoked" : relative(k.last_used_at)}</td>
                <td className="r">{!k.revoked_at && <Button size="sm" variant="ghost" onClick={() => setRevoke(k)}>Revoke</Button>}</td>
              </tr>))}</tbody></table></div>
        )}
      </Panel>
      <Panel title="Create an invoice" eyebrow="Merchant API v1"
        actions={<a className="btn btn--ghost btn--sm" href="/api/docs" target="_blank" rel="noreferrer">API reference ↗</a>}>
        <pre className="code">{EXAMPLE(window.location.origin)}</pre>
      </Panel>
      {creating && <CreateKey scopes={keys.data.scopes} onClose={() => setCreating(false)}
        onCreated={(k) => { setCreating(false); setCreated(k); keys.reload(); }} />}
      <Dialog open={!!created} onClose={() => setCreated(null)} title="Your new API key"
        description="Copy it now and store it as a secret in your store's backend. Never embed it in browser code."
        footer={<Button variant="primary" onClick={() => setCreated(null)}>Done</Button>}>
        {created && <CopyField value={created} label="API key" />}
      </Dialog>
      <Dialog open={!!revoke} onClose={() => setRevoke(null)} title={`Revoke "${revoke?.name}"?`}
        description="Requests using this key will fail immediately. This cannot be undone."
        footer={<><Button variant="ghost" onClick={() => setRevoke(null)}>Cancel</Button>
          <Button variant="danger" onClick={async () => {
            await api.post(`/api/admin/api-keys/${revoke!.id}/revoke`);
            toast("success", "Key revoked");
            setRevoke(null);
            keys.reload();
          }}>Revoke key</Button></>} />
    </>
  );
}

function CreateKey({ scopes, onClose, onCreated }: { scopes: string[]; onClose: () => void; onCreated: (k: string) => void }) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"live" | "test">("test");
  const [sel, setSel] = useState<string[]>(scopes);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog open onClose={onClose} title="Create API key"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!name || !sel.length}
        onClick={async () => {
          try { onCreated((await api.post<{ key: string }>("/api/admin/api-keys", { name, mode, scopes: sel })).key); }
          catch (e) { setError((e as Error).message); }
        }}>Create</Button></>}>
      {error && <Alert tone="danger">{error}</Alert>}
      <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Storefront production" autoFocus />
      <Segmented label="Mode" value={mode} onChange={setMode} options={[{ value: "test", label: "Test" }, { value: "live", label: "Live" }]} />
      <div className="stack--sm">{scopes.map((s) => (
        <Check key={s} label={<span className="mono xs">{s}</span>} checked={sel.includes(s)}
          onChange={(e) => setSel(e.target.checked ? [...sel, s] : sel.filter((x) => x !== s))} />))}</div>
    </Dialog>
  );
}
