import { useState } from "react";
import { Alert, Button, CopyField, Dialog, Empty, ErrorBlock, Kv, LoadingBlock, PageHead, Panel, Segmented, Select, Status,
  Textarea, useToast } from "../components/ui";
import { api } from "../lib/api";
import { relative, shortHash } from "../lib/format";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import type { NetworkRow, SweepRow } from "../lib/types";

export function Sweeps() {
  const { can, user } = useSession();
  const toast = useToast();
  const [filter, setFilter] = useState<"action" | "active" | "all">("action");
  const statuses = filter === "action" ? "awaiting_approval,awaiting_external_signature" :
    filter === "active" ? "planned,approved,signing,broadcast" : "";
  const list = useApi<{ items: SweepRow[]; treasury: { sweeps_halted: boolean; require_approval: boolean } }>(
    `/api/admin/sweeps${statuses ? `?status=${statuses}` : ""}`, 10000);
  const nets = useApi<NetworkRow[]>("/api/admin/networks");
  const [open, setOpen] = useState<SweepRow | null>(null);
  const [halting, setHalting] = useState(false);
  const [planNet, setPlanNet] = useState("");
  const [busy, setBusy] = useState(false);
  if (!list.data) return list.error ? <ErrorBlock error={list.error} /> : <LoadingBlock />;
  const halted = list.data.treasury.sweeps_halted;

  async function act(path: string, body?: unknown, ok?: string) {
    setBusy(true);
    try {
      const r = await api.post<SweepRow>(path, body);
      if (ok) toast("success", ok);
      list.reload();
      return r;
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead title="Consolidation" actions={can("admin") && (halted
        ? <Button variant="primary" icon="refresh" disabled={user?.role !== "owner"} onClick={() => setHalting(true)}
          title={user?.role !== "owner" ? "Only the owner can resume" : undefined}>Resume treasury</Button>
        : <Button variant="danger" icon="halt" onClick={() => setHalting(true)}>Emergency stop</Button>)}>
        Sweeps move confirmed deposits to your primary wallets. Each one is planned from live chain balances, fee-capped,
        idempotent, and audited. The emergency stop halts planning, signing and broadcast instantly.
      </PageHead>

      {can("admin") && (
        <Panel>
          <div className="row" style={{ alignItems: "flex-end" }}>
            <div style={{ minWidth: 240 }}>
              <Select label="Plan a sweep now" value={planNet} onChange={(e) => setPlanNet(e.target.value)}>
                <option value="">Select network…</option>
                {(nets.data ?? []).filter((n) => n.wallet?.destination).map((n) => <option key={n.key} value={n.key}>{n.name}</option>)}
              </Select>
            </div>
            <Button disabled={!planNet || halted} loading={busy} onClick={async () => {
              const r = await act(`/api/admin/sweeps/plan/${planNet}`) as unknown as { created: SweepRow[] } | undefined;
              if (r) toast(r.created.length ? "success" : "info", r.created.length ? `${r.created.length} sweep(s) planned` : "Nothing eligible to sweep");
            }}>Plan</Button>
            <span className="xs muted grow">Only deposits of settled invoices (or past their late-payment window) are eligible.</span>
          </div>
        </Panel>
      )}

      <div className="filters">
        <Segmented label="Filter" value={filter} onChange={setFilter} options={[
          { value: "action", label: "Needs action" }, { value: "active", label: "In flight" }, { value: "all", label: "History" }]} />
      </div>

      <Panel flush>
        {list.data.items.length === 0 ? <Empty icon="sweep" title="Nothing here">No sweeps in this view.</Empty> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Network</th><th>Kind</th><th className="r">Amount</th><th className="r">Fee est.</th><th>Destination</th><th>Status</th><th className="r">Updated</th><th /></tr></thead>
            <tbody>{list.data.items.map((s) => (
              <tr key={s.id} data-href onClick={() => setOpen(s)}>
                <td className="small nowrap">{s.network_name}</td>
                <td><span className="tag">{s.kind === "gas_topup" ? "gas top-up" : "sweep"}</span></td>
                <td className="r num nowrap">{s.amount} {s.symbol}</td>
                <td className="r num xs muted nowrap">{s.fee_estimate} {s.fee_symbol}</td>
                <td className="mono xs">{shortHash(s.destination, 8, 6)}</td>
                <td><Status value={s.status} /></td>
                <td className="r xs muted nowrap">{relative(s.updated_at)}</td>
                <td className="r"><Button size="sm" variant="ghost" iconRight="chevron">Open</Button></td>
              </tr>))}</tbody></table></div>
        )}
      </Panel>

      <SweepDialog sweep={open} onClose={() => setOpen(null)} canAct={can("admin") && !halted} busy={busy}
        onApprove={(s) => act(`/api/admin/sweeps/${s.id}/approve`, undefined, "Sweep approved").then(() => setOpen(null))}
        onCancel={(s) => act(`/api/admin/sweeps/${s.id}/cancel`, undefined, "Sweep cancelled").then(() => setOpen(null))}
        onSubmit={(s, signed) => act(`/api/admin/sweeps/${s.id}/submit`, { signed }, "Transaction broadcast").then((r) => r && setOpen(null))} />

      <HaltDialog open={halting} halted={halted} onClose={() => setHalting(false)} onDone={() => { setHalting(false); list.reload(); }} />
    </>
  );
}

function SweepDialog({ sweep: s, onClose, canAct, busy, onApprove, onCancel, onSubmit }: {
  sweep: SweepRow | null; onClose: () => void; canAct: boolean; busy: boolean;
  onApprove: (s: SweepRow) => void; onCancel: (s: SweepRow) => void; onSubmit: (s: SweepRow, signed: string) => void;
}) {
  const [signed, setSigned] = useState("");
  if (!s) return null;
  const a = s.artifact as { psbt?: string; tx?: unknown; intent?: unknown; instructions?: string; subwallet_id?: number; index?: number; role?: string };
  const exportJson = JSON.stringify({ network: s.network, sweep_id: s.id, artifact: s.artifact }, null, 2);
  const download = () => {
    const blob = new Blob([exportJson], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const el = document.createElement("a");
    el.href = url;
    el.download = `cryptus-sweep-${s.id}.json`;
    el.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Dialog wide open onClose={onClose} title={`${s.kind === "gas_topup" ? "Gas top-up" : "Sweep"} · ${s.network_name}`}
      footer={canAct && <>
        {["planned", "awaiting_approval", "approved", "awaiting_external_signature"].includes(s.status) &&
          <Button variant="danger" onClick={() => onCancel(s)} loading={busy}>Cancel sweep</Button>}
        {s.status === "awaiting_approval" && <Button variant="primary" onClick={() => onApprove(s)} loading={busy}>Approve</Button>}
        {s.status === "awaiting_external_signature" &&
          <Button variant="primary" onClick={() => onSubmit(s, signed)} loading={busy} disabled={signed.length < 10}>Broadcast signed transaction</Button>}
      </>}>
      <div className="row"><Status value={s.status} />{s.error && <span className="xs" style={{ color: "var(--danger)" }}>{s.error}</span>}</div>
      <Kv items={[
        ["Amount", <span className="num">{s.amount} {s.symbol}</span>],
        ["Estimated fee", <span className="num">{s.fee_estimate} {s.fee_symbol}</span>],
        ["From", s.source ? <span className="mono xs break">{s.source}</span> : `${s.deposits} deposit address(es)`],
        ["To", <span className="mono xs break">{s.destination}</span>],
        ["Transaction", s.txid ? (s.explorer_url ? <a className="mono xs" href={s.explorer_url} target="_blank" rel="noreferrer noopener">{shortHash(s.txid, 12, 8)} ↗</a> : <span className="mono xs">{s.txid}</span>) : "—"],
        ["Attempts", <span className="num">{s.attempts}</span>],
        ...(s.depends_on ? [["Depends on", <span className="mono xs">gas top-up {shortHash(s.depends_on, 6, 4)}</span>] as [string, JSX.Element]] : []),
      ]} />
      {s.status === "awaiting_external_signature" && (
        <div className="stack">
          {a.instructions && <Alert tone="info">{a.instructions}</Alert>}
          {a.psbt ? (
            <div className="stack--sm"><span className="label">Unsigned PSBT (base64)</span><CopyField value={a.psbt} label="PSBT" /></div>
          ) : (
            <div className="stack--sm"><span className="label">Signing request</span><pre className="code">{exportJson}</pre></div>
          )}
          <div><Button size="sm" icon="download" onClick={download}>Download for offline signer</Button></div>
          <Textarea label="Signed transaction" mono rows={4} value={signed} onChange={(e) => setSigned(e.target.value.trim())}
            hint="Signed PSBT or raw transaction hex (Bitcoin), 0x-prefixed raw transaction (EVM), or BOC base64 (TON). CRYPTUS verifies it matches this plan before broadcasting." />
        </div>
      )}
    </Dialog>
  );
}

function HaltDialog({ open, halted, onClose, onDone }: { open: boolean; halted: boolean; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog open={open} onClose={onClose} title={halted ? "Resume treasury operations?" : "Engage emergency stop?"}
      description={halted ? "Planning, signing and broadcast resume on the next worker cycle." :
        "Immediately stops all consolidation: nothing new is planned, approved, signed or broadcast. Payment detection continues."}
      footer={<><Button variant="ghost" onClick={onClose}>Close</Button>
        <Button variant={halted ? "primary" : "danger"} loading={pending} onClick={async () => {
          setPending(true);
          try { await api.post("/api/admin/treasury/halt", { halted: !halted, reason }); onDone(); }
          catch (e) { setError((e as Error).message); } finally { setPending(false); }
        }}>{halted ? "Resume" : "Stop everything"}</Button></>}>
      {error && <Alert tone="danger">{error}</Alert>}
      <Textarea label="Reason (audit log)" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
      {!halted && <Alert tone="warning">The isolated signer has its own stop: create a <code>HALT</code> file next to its policy to refuse all signing regardless of the gateway.</Alert>}
    </Dialog>
  );
}
