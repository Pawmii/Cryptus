import { useState } from "react";
import { api } from "../lib/api";
import type { NetworkRow } from "../lib/types";
import { Alert, Button, Input, Select, Switch, useToast } from "./ui";

const BACKEND_LABEL: Record<string, string> = {
  esplora: "Esplora REST (electrs / mempool)", bitcoin_core: "Bitcoin Core RPC (watch-only wallet)",
  jsonrpc: "EVM JSON-RPC", toncenter: "Toncenter API v2/v3",
};

export const ENDPOINT_HINT: Record<string, string> = {
  "btc:esplora": "https://mempool.space/api", "btc-testnet:esplora": "https://mempool.space/testnet4/api",
  "btc:bitcoin_core": "http://bitcoind:8332", "btc-testnet:bitcoin_core": "http://bitcoind:48332",
  "ton:toncenter": "https://toncenter.com", "ton-testnet:toncenter": "https://testnet.toncenter.com",
};

export function NetworkForm({ net, onSaved, compact }: { net: NetworkRow; onSaved: (n: NetworkRow) => void; compact?: boolean }) {
  const toast = useToast();
  const [backend, setBackend] = useState(net.backend ?? net.backends[0]);
  const [url, setUrl] = useState(net.endpoint_url ?? "");
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [confs, setConfs] = useState(String(net.confirmations));
  const [enabled, setEnabled] = useState(net.enabled);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState(net.health);

  const credFields: [string, string, string][] =
    backend === "bitcoin_core" ? [["username", "RPC username", "text"], ["password", "RPC password", "password"]]
      : backend === "toncenter" ? [["api_key", "API key", "password"]]
        : [["bearer", "Bearer token (optional)", "password"]];

  async function save(enable?: boolean) {
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { backend, endpoint_url: url, confirmations: Number(confs) };
      if (Object.values(creds).some(Boolean)) body.credentials = creds;
      if (enable !== undefined) body.enabled = enable;
      else body.enabled = enabled;
      const n = await api.put<NetworkRow>(`/api/admin/networks/${net.key}`, body);
      setEnabled(n.enabled);
      setCreds({});
      onSaved(n);
      toast("success", `${net.name} saved`);
      return n;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    setError(null);
    try {
      if (url !== (net.endpoint_url ?? "") || backend !== net.backend || Object.values(creds).some(Boolean)) await save();
      setHealth(await api.post(`/api/admin/networks/${net.key}/test`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="stack">
      {error && <Alert tone="danger">{error}</Alert>}
      <div className={compact ? "stack" : "grid-2"}>
        <Select label="Backend" value={backend} onChange={(e) => setBackend(e.target.value)}>
          {net.backends.map((b) => <option key={b} value={b}>{BACKEND_LABEL[b] ?? b}</option>)}
        </Select>
        <Input label="Endpoint URL" mono value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder={ENDPOINT_HINT[`${net.key}:${backend}`] ?? "https://your-node.internal:8545"}
          hint="Prefer your own node: third-party endpoints can observe which addresses you watch." />
      </div>
      <div className={compact ? "stack" : "grid-3"}>
        {credFields.map(([k, label, type]) => (
          <Input key={k} label={label} type={type} mono autoComplete="off" value={creds[k] ?? ""}
            placeholder={net.has_credentials ? "•••••• stored (leave blank to keep)" : ""}
            onChange={(e) => setCreds((c) => ({ ...c, [k]: e.target.value }))} />
        ))}
        <Input label="Confirmations required" type="number" min={1} max={1000} value={confs} mono
          onChange={(e) => setConfs(e.target.value)}
          hint={Number(confs) < net.default_confirmations ? `Below the recommended ${net.default_confirmations} — reorg risk.`
            : `Recommended: ${net.default_confirmations}`} />
      </div>
      {health && health.ok !== undefined && (
        <Alert tone={health.ok ? "success" : "danger"} title={health.ok ? "Backend reachable" : "Health check failed"}>
          {health.ok
            ? <span className="num">height {health.height?.toLocaleString()} · {health.latency_ms} ms</span>
            : health.error}
        </Alert>
      )}
      <div className="row row--between">
        <Switch label={enabled ? "Monitoring enabled" : "Monitoring disabled"} checked={enabled}
          disabled={!url} onChange={(e) => save(e.target.checked)} />
        <div className="row">
          <Button variant="ghost" icon="pulse" onClick={test} loading={testing} disabled={!url}>Test connection</Button>
          <Button variant="primary" onClick={() => save()} loading={saving} disabled={!url}>Save</Button>
        </div>
      </div>
    </div>
  );
}
