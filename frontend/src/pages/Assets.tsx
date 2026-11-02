import { useState } from "react";
import { Alert, Button, Dialog, ErrorBlock, Input, LoadingBlock, PageHead, Panel, Select, Switch, useToast } from "../components/ui";
import { api } from "../lib/api";
import { shortHash } from "../lib/format";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import type { AssetRow, NetworkRow } from "../lib/types";

export function Assets() {
  const assets = useApi<AssetRow[]>("/api/admin/assets");
  const nets = useApi<NetworkRow[]>("/api/admin/networks");
  const { can } = useSession();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  if (!assets.data) return assets.error ? <ErrorBlock error={assets.error} /> : <LoadingBlock />;

  async function patch(a: AssetRow, body: Partial<AssetRow>) {
    try {
      const n = await api.patch<AssetRow>(`/api/admin/assets/${encodeURIComponent(a.id)}`, body);
      assets.setData(assets.data!.map((x) => (x.id === n.id ? n : x)));
    } catch (e) {
      toast("error", (e as Error).message);
    }
  }

  return (
    <>
      <PageHead title="Assets" actions={can("admin") && <Button size="sm" variant="primary" icon="plus" onClick={() => setAdding(true)}>Add token</Button>}>
        Assets are identified by canonical on-chain ids, never by ticker. Tokens added here are verified on-chain
        (contract code and decimals) before they can be quoted.
      </PageHead>
      <Panel flush>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Asset</th><th>Network</th><th>Canonical id</th><th className="r">Decimals</th><th>Price source</th><th>Enabled</th></tr></thead>
          <tbody>{assets.data.map((a) => (
            <tr key={a.id}>
              <td><div><strong style={{ fontWeight: 600 }}>{a.symbol}</strong><span className="xs faint">{a.name}</span></div></td>
              <td className="small nowrap">{a.network_name}<div className="xs faint">{a.kind === "native" ? "native" : a.kind === "jetton" ? "Jetton" : "ERC-20"}</div></td>
              <td className="mono xs" title={a.id}>{a.contract ? `${a.network}:${a.kind}:${shortHash(a.contract, 8, 6)}` : a.id}</td>
              <td className="r num">{a.decimals}</td>
              <td className="small">{a.fixed_usd_peg ? <span className="tag">1:1 USD peg</span> : a.coingecko_id ?? <span className="faint">none</span>}</td>
              <td>{can("admin") ? <Switch label="" checked={a.enabled} onChange={(e) => patch(a, { enabled: e.target.checked })} aria-label={`Enable ${a.symbol}`} />
                : a.enabled ? "yes" : "no"}</td>
            </tr>))}</tbody></table></div>
      </Panel>
      <AddToken open={adding} onClose={() => setAdding(false)} nets={(nets.data ?? []).filter((n) => ["evm", "ton"].includes(n.family) && n.endpoint_url)}
        onAdded={() => { setAdding(false); assets.reload(); }} />
    </>
  );
}

function AddToken({ open, onClose, nets, onAdded }: { open: boolean; onClose: () => void; nets: NetworkRow[]; onAdded: () => void }) {
  const [f, setF] = useState({ network: "", contract: "", symbol: "", name: "", decimals: "", coingecko_id: "" });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const net = nets.find((n) => n.key === f.network);
  async function submit() {
    setPending(true);
    setError(null);
    try {
      await api.post("/api/admin/assets", { ...f, decimals: Number(f.decimals), coingecko_id: f.coingecko_id || null });
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  return (
    <Dialog open={open} onClose={onClose} title="Add a token" description="The contract is inspected on-chain through the network's configured endpoint."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={submit} loading={pending}
        disabled={!f.network || !f.contract || !f.symbol || f.decimals === ""}>Verify & add</Button></>}>
      {error && <Alert tone="danger">{error}</Alert>}
      <Select label="Network" value={f.network} onChange={(e) => setF({ ...f, network: e.target.value })}>
        <option value="">Select…</option>
        {nets.map((n) => <option key={n.key} value={n.key}>{n.name}</option>)}
      </Select>
      <Input label={net?.family === "ton" ? "Jetton master address" : "Token contract address"} mono value={f.contract}
        onChange={(e) => setF({ ...f, contract: e.target.value.trim() })} />
      <div className="grid-3">
        <Input label="Symbol" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })} />
        <Input label="Decimals" type="number" min={0} max={36} value={f.decimals} onChange={(e) => setF({ ...f, decimals: e.target.value })} />
        <Input label="CoinGecko id" value={f.coingecko_id} onChange={(e) => setF({ ...f, coingecko_id: e.target.value })} placeholder="optional" />
      </div>
      <Input label="Display name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <Alert tone="warning">Tickers are not unique. Confirm the contract address from the issuer's official source — never from a search result.</Alert>
    </Dialog>
  );
}
