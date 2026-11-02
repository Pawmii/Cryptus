import { useState } from "react";
import { NetworkForm } from "../components/NetworkForm";
import { Dialog, ErrorBlock, LoadingBlock, Panel, PageHead, Segmented, Status, Button } from "../components/ui";
import { relative } from "../lib/format";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import type { NetworkRow } from "../lib/types";

const FAMILY_LABEL: Record<string, string> = { bitcoin: "UTXO", evm: "EVM", ton: "TON", litecoin: "UTXO", dogecoin: "UTXO", tron: "TVM", solana: "SVM" };

export function Networks() {
  const nets = useApi<NetworkRow[]>("/api/admin/networks", 30000);
  const { can } = useSession();
  const [env, setEnv] = useState<"mainnet" | "testnet">("mainnet");
  const [edit, setEdit] = useState<NetworkRow | null>(null);
  if (!nets.data) return nets.error ? <ErrorBlock error={nets.error} retry={nets.reload} /> : <LoadingBlock />;
  const rows = nets.data.filter((n) => n.environment === env && n.implementation === "implemented");
  const planned = nets.data.filter((n) => n.implementation === "planned");

  return (
    <>
      <PageHead title="Networks">Each network is an independent adapter with its own backend, confirmation policy and health
        checks. A network accepts payments only when it is configured, enabled, healthy and has a verified wallet.</PageHead>
      <div className="filters">
        <Segmented label="Environment" value={env} onChange={setEnv}
          options={[{ value: "mainnet", label: "Mainnet" }, { value: "testnet", label: "Testnet" }]} />
      </div>
      <Panel flush>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Network</th><th>Status</th><th>Backend</th><th className="r">Height</th><th className="r">Latency</th>
            <th className="r">Confirmations</th><th>Wallet</th><th /></tr></thead>
          <tbody>{rows.map((n) => (
            <tr key={n.key}>
              <td><div><span className="row" style={{ gap: 8 }}>{n.name}<span className="tag">{n.native_symbol}</span></span>
                <span className="xs faint">{FAMILY_LABEL[n.family]}{n.chain_id ? ` · chain ${n.chain_id}` : ""}</span></div></td>
              <td><Status value={n.status} live={n.status === "operational"} />
                {n.health?.error && <div className="xs" style={{ color: "var(--danger)", maxWidth: 260 }}>{n.health.error}</div>}</td>
              <td className="small muted">{n.endpoint_url ? n.backend : "—"}</td>
              <td className="r num small">{n.health?.height ? n.health.height.toLocaleString() : "—"}</td>
              <td className="r num small muted">{n.health?.latency_ms ? `${n.health.latency_ms} ms` : "—"}
                {n.health_checked_at && <div className="xs faint">{relative(n.health_checked_at)}</div>}</td>
              <td className="r num">{n.confirmations}</td>
              <td className="small">{n.wallet ? <span className="row" style={{ gap: 6 }}>{n.wallet.keychain?.label}
                <span className="tag">{n.wallet.keychain?.mode === "managed" ? "managed" : "watch-only"}</span></span> : <span className="faint">none</span>}</td>
              <td className="r">{can("admin") && <Button size="sm" variant="ghost" onClick={() => setEdit(n)}>Configure</Button>}</td>
            </tr>))}</tbody></table></div>
      </Panel>

      <Panel title="Planned adapters" eyebrow="Not available in this release">
        <div className="grid-4">
          {planned.map((n) => (
            <div key={n.key} className="stack--sm">
              <div className="row"><strong style={{ fontWeight: 600 }}>{n.name}</strong><Status value="planned" /></div>
              <p className="xs muted">{n.note}</p>
            </div>
          ))}
        </div>
      </Panel>

      <Dialog wide open={!!edit} onClose={() => setEdit(null)} title={edit ? `Configure ${edit.name}` : ""}
        description="Credentials are encrypted at rest and never returned by the API.">
        {edit && <NetworkForm net={edit} onSaved={() => nets.reload()} />}
      </Dialog>
    </>
  );
}
