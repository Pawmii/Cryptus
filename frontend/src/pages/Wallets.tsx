import { useState } from "react";
import { KeychainSetup, familyScope } from "../components/KeychainSetup";
import { Alert, Button, CopyField, Dialog, ErrorBlock, Input, Kv, LoadingBlock, Panel, PageHead, Select, Status, Switch,
  useToast } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, shortHash } from "../lib/format";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import type { AssetRow, Keychain, NetworkRow, WalletInfo } from "../lib/types";

export function Wallets() {
  const nets = useApi<NetworkRow[]>("/api/admin/networks");
  const kcs = useApi<{ items: Keychain[]; signer: { configured: boolean } }>("/api/admin/keychains");
  const assets = useApi<AssetRow[]>("/api/admin/assets");
  const { can } = useSession();
  const toast = useToast();
  const [setup, setSetup] = useState<NetworkRow | null>(null);
  const [config, setConfig] = useState<NetworkRow | null>(null);
  const [change, setChange] = useState<NetworkRow | null>(null);
  if (!nets.data || !kcs.data) return nets.error ? <ErrorBlock error={nets.error} /> : <LoadingBlock />;
  const relevant = nets.data.filter((n) => n.implementation === "implemented" && (n.endpoint_url || n.wallet));
  const reload = () => { nets.reload(); kcs.reload(); };

  async function importSigner() {
    try {
      const r = await api.post<{ created: Keychain[] }>("/api/admin/keychains/import-signer");
      toast("success", `${r.created.length} managed keychain(s) imported`);
      reload();
    } catch (e) {
      toast("error", (e as Error).message);
    }
  }

  return (
    <>
      <PageHead title="Wallets" actions={can("admin") && kcs.data.signer.configured &&
        <Button size="sm" icon="signer" onClick={importSigner}>Import signer keys</Button>}>
        Deposit addresses derive from public keys you control. <strong>Watch-only</strong> keychains never sign — consolidation is
        exported for your hardware or offline wallet. <strong>Managed</strong> keychains belong to the isolated signer, which enforces its own policy.
        On TON, <strong>direct</strong> mode sends every payment straight to your own wallet, matched by a per-invoice comment.
      </PageHead>

      {relevant.length === 0 && <Alert tone="info">Configure a network first; wallets attach per network.</Alert>}
      <div className="stack">
        {relevant.map((n) => (
          <Panel key={n.key} title={<span className="row">{n.name}<span className="tag">{n.environment}</span></span>}
            actions={<>
              {n.wallet ? <Status value={n.wallet.keychain?.verified_at ? "active" : "pending"} /> : <Status value="not_configured" />}
              {can("admin") && (n.wallet ? <>
                {n.wallet.keychain?.mode !== "direct" && <Button size="sm" variant="ghost" onClick={() => setConfig(n)}>Consolidation</Button>}
                <Button size="sm" variant="ghost" onClick={() => setChange(n)}>Change wallet</Button>
              </> : <Button size="sm" variant="primary" icon="plus" onClick={() => setSetup(n)}>Attach wallet</Button>)}
            </>}>
            {n.wallet?.keychain ? <WalletSummary w={n.wallet} /> : <AttachExisting net={n} keychains={kcs.data!.items} onDone={reload} />}
          </Panel>
        ))}
      </div>

      <Panel title="Keychains" eyebrow="Public key material" flush>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Label</th><th>Family</th><th>Mode</th><th>Material</th><th className="r">Next index</th><th>Verified</th></tr></thead>
          <tbody>{kcs.data.items.map((k) => (
            <tr key={k.id}>
              <td>{k.label}{!k.active && <span className="tag" style={{ marginLeft: 6 }}>inactive</span>}</td>
              <td className="small">{k.family} · {k.scope}</td>
              <td><span className="tag">{modeLabel(k.mode)}</span></td>
              <td className="mono xs" title={k.public_material}>{k.public_material_short}</td>
              <td className="r num">{k.next_index}</td>
              <td className="small">{k.verified_at ? dateTime(k.verified_at) : <span style={{ color: "var(--warning)" }}>unverified</span>}</td>
            </tr>))}</tbody></table></div>
      </Panel>

      <Dialog wide open={!!setup} onClose={() => setSetup(null)} title={setup ? `Attach a ${setup.name} wallet` : ""}>
        {setup && <KeychainSetup net={setup} onDone={() => { setSetup(null); reload(); }} />}
      </Dialog>
      <Dialog open={!!change} onClose={() => setChange(null)} title={change ? `Change the ${change.name} wallet` : ""}
        description="New invoices will use the wallet you choose. Existing invoices keep their addresses and are still watched.">
        {change && (
          <div className="stack">
            <Alert tone="warning">Funds on per-order addresses of the current keychain are collected only while it is attached.
              Collect them (Consolidation → Plan) before switching, or switch back later.</Alert>
            <AttachExisting net={change} keychains={kcs.data.items.filter((k) => k.id !== change.wallet?.keychain_id)}
              onDone={() => { setChange(null); reload(); toast("success", "Wallet changed"); }}
              empty="No other verified keychain for this network. Set up a new one, or import the signer's keys." />
            <div className="row row--end">
              <Button variant="ghost" icon="plus" onClick={() => { const n = change; setChange(null); setSetup(n); }}>Set up a new one</Button>
            </div>
          </div>
        )}
      </Dialog>
      <Dialog wide open={!!config} onClose={() => setConfig(null)} title={config ? `${config.name} consolidation` : ""}
        description="Sweeps move confirmed deposit balances into your primary wallet. Every sweep is planned from on-chain balances, fee-capped, and (by default) waits for approval.">
        {config?.wallet && <ConsolidationForm net={config} w={config.wallet}
          assets={(assets.data ?? []).filter((a) => a.network === config.key)} onSaved={() => { setConfig(null); reload(); }} />}
      </Dialog>
    </>
  );
}

const modeLabel = (m: Keychain["mode"]) => (m === "managed" ? "managed" : m === "direct" ? "direct" : "watch-only");

function WalletSummary({ w }: { w: WalletInfo }) {
  const k = w.keychain!;
  if (k.mode === "direct") {
    return (
      <div className="grid-2">
        <Kv items={[
          ["Receiving wallet", <span className="mono xs break">{w.receiving_address}</span>],
          ["Mode", <><span className="tag">direct</span> <span className="small muted">unique comment per invoice</span></>],
          ["Invoices issued", <span className="num">{k.next_index}</span>],
        ]} />
        <Kv items={[
          ["Label", k.label],
          ["Consolidation", <span className="muted">not needed — payments arrive in your wallet</span>],
          ["Keys on this server", <span className="muted">none</span>],
        ]} />
      </div>
    );
  }
  return (
    <div className="grid-2">
      <Kv items={[
        ["Keychain", <>{k.label} <span className="tag">{modeLabel(k.mode)}</span></>],
        ["Public material", <span className="mono xs break">{shortHash(k.public_material, 16, 10)}</span>],
        ...(k.master_fingerprint ? [["Fingerprint · path", <span className="mono xs">{k.master_fingerprint} · {k.account_path}</span>] as [string, JSX.Element]] : []),
        ["Addresses issued", <span className="num">{k.next_index}</span>],
      ]} />
      <Kv items={[
        ["Consolidation", w.sweep_enabled ? <Status value="active" /> : <span className="muted">off</span>],
        ["Destination", w.destination ? <span className="mono xs break">{w.destination}</span> : <span className="faint">not set</span>],
        ...(w.gas_address ? [["Gas wallet", <span className="mono xs break" title="Funds fee top-ups for token sweeps">{shortHash(w.gas_address, 10, 8)}</span>] as [string, JSX.Element]] : []),
        ["Interval", `${w.sweep_interval_minutes} min`],
      ]} />
    </div>
  );
}

function AttachExisting({ net, keychains, onDone, empty = "No verified keychain for this network yet." }: {
  net: NetworkRow; keychains: Keychain[]; onDone: () => void; empty?: string;
}) {
  const toast = useToast();
  const options = keychains.filter((k) => k.family === net.family && k.scope === familyScope(net) && k.verified_at && k.active);
  const [sel, setSel] = useState("");
  if (!options.length) return <p className="small muted">{empty}</p>;
  return (
    <div className="row" style={{ alignItems: "flex-end" }}>
      <div className="grow" style={{ maxWidth: 360 }}>
        <Select label="Use an existing keychain" value={sel} onChange={(e) => setSel(e.target.value)}>
          <option value="">Select…</option>
          {options.map((k) => <option key={k.id} value={k.id}>{k.label} ({modeLabel(k.mode)})</option>)}
        </Select>
      </div>
      <Button disabled={!sel} onClick={async () => {
        try { await api.put(`/api/admin/wallets/${net.key}`, { keychain_id: sel }); onDone(); }
        catch (e) { toast("error", (e as Error).message); }
      }}>Attach</Button>
    </div>
  );
}

function ConsolidationForm({ net, w, assets, onSaved }: { net: NetworkRow; w: WalletInfo; assets: AssetRow[]; onSaved: () => void }) {
  const toast = useToast();
  const [dest, setDest] = useState(w.destination ?? "");
  const [enabled, setEnabled] = useState(w.sweep_enabled);
  const [interval, setInterval_] = useState(String(w.sweep_interval_minutes));
  const [thresholds, setThresholds] = useState<Record<string, string>>(w.sweep_thresholds);
  const [maxFee, setMaxFee] = useState(w.max_fee.native ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const destChanged = dest !== (w.destination ?? "");

  async function save() {
    setPending(true);
    setError(null);
    try {
      if (destChanged) await api.put(`/api/admin/wallets/${net.key}`, { set_destination: true, destination: dest || null });
      await api.put(`/api/admin/wallets/${net.key}`, {
        sweep_enabled: enabled, sweep_interval_minutes: Number(interval),
        sweep_thresholds: Object.fromEntries(Object.entries(thresholds).filter(([, v]) => v)),
        max_fee: maxFee ? { native: maxFee } : {},
      });
      toast("success", "Consolidation policy saved");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="stack">
      {error && <Alert tone="danger">{error}</Alert>}
      <Input label="Primary wallet (destination)" mono value={dest} onChange={(e) => setDest(e.target.value)}
        hint="Saving a destination records you as the confirming operator. Managed keychains also require it on the signer's own allowlist." />
      {w.destination && !destChanged && <CopyField value={w.destination} label="destination" />}
      <div className="grid-3">
        <Input label="Run every" type="number" min={10} suffix="min" value={interval} onChange={(e) => setInterval_(e.target.value)} />
        <Input label="Max network fee per sweep" mono value={maxFee} suffix={net.native_symbol}
          onChange={(e) => setMaxFee(e.target.value)} hint="Sweeps above this fee are skipped." />
      </div>
      <div className="stack--sm">
        <span className="label">Minimum balance to sweep</span>
        <div className="grid-3">
          {assets.map((a) => (
            <Input key={a.id} label={a.symbol} mono value={thresholds[a.id] ?? ""} placeholder="any" suffix={a.symbol}
              onChange={(e) => setThresholds({ ...thresholds, [a.id]: e.target.value })} />
          ))}
        </div>
      </div>
      {w.keychain?.mode === "watch_only" && <Alert tone="info">Watch-only: sweeps are prepared as unsigned transactions (a PSBT for Bitcoin)
        for you to sign externally on the Consolidation page.</Alert>}
      <div className="row row--between">
        <Switch label="Automatic planning" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} disabled={!dest && !w.destination} />
        <Button variant="primary" onClick={save} loading={pending}>Save policy</Button>
      </div>
    </div>
  );
}
