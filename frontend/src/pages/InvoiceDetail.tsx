import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Alert, Button, Confirmations, CopyField, CopyInline, Dialog, ErrorBlock, Kv, LoadingBlock, Panel, Status,
  Textarea, useToast } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, isFiat, money, relative, shortHash } from "../lib/format";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import type { InvoiceFull } from "../lib/types";

export function InvoiceDetail() {
  const { id } = useParams();
  const { can } = useSession();
  const toast = useToast();
  const inv = useApi<InvoiceFull>(`/api/admin/invoices/${id}`, 8000);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  if (!inv.data) return inv.error ? <ErrorBlock error={inv.error} retry={inv.reload} /> : <LoadingBlock />;
  const i = inv.data;
  const p = i.payment;

  async function cancel() {
    setBusy(true);
    try {
      inv.setData(await api.post<InvoiceFull>(`/api/admin/invoices/${i.id}/cancel`));
      toast("success", "Invoice cancelled");
      setConfirmCancel(false);
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function resolve() {
    setBusy(true);
    try {
      inv.setData(await api.post<InvoiceFull>(`/api/admin/invoices/${i.id}/resolve`, { note }));
      setResolving(false);
      toast("success", "Review resolved");
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div className="stack--sm">
          <Link to="/invoices" className="xs muted">← Invoices</Link>
          <div className="row">
            <h1 className="mono" style={{ fontStretch: "100%" }}>{i.number}</h1>
            <Status value={i.status} live={i.status === "processing"} />
            {i.mode === "test" && <Status value="test" />}
          </div>
        </div>
        <div className="row">
          <a className="btn btn--secondary btn--sm" href={`/pay/${i.id}`} target="_blank" rel="noreferrer">Open checkout ↗</a>
          {can("operator") && ["pending", "processing"].includes(i.status) &&
            <Button size="sm" variant="danger" onClick={() => setConfirmCancel(true)}>Cancel invoice</Button>}
        </div>
      </div>

      {i.flags.needs_review && (
        <Alert tone="accent" title="Needs operator review">
          <div className="row">Funds arrived to more than one quote or after cancellation. Reconcile with the payer, then mark it resolved.
            {can("admin") && <Button size="sm" variant="secondary" onClick={() => setResolving(true)}>Mark resolved</Button>}</div>
        </Alert>
      )}
      {i.flags.overpaid && <Alert tone="info" title="Overpaid">The payer sent more than the amount due. Arrange a refund off-platform if required.</Alert>}
      {i.flags.paid_late && <Alert tone="warning" title="Paid after expiry">The first payment was detected after the quote expired; the locked rate was honoured.</Alert>}

      <div className="grid-2" style={{ alignItems: "start" }}>
        <Panel title="Payment" eyebrow={p ? `${p.symbol} · ${p.network_name}` : "Awaiting payer selection"}>
          {p ? (
            <div className="stack">
              <div className="grid-2">
                <div className="stack--sm"><span className="label">Due</span>
                  <span className="stat__value" style={{ fontSize: "var(--text-xl)" }}>{p.amount_due} <small>{p.symbol}</small></span></div>
                <div className="stack--sm"><span className="label">Confirmed</span>
                  <span className="stat__value" style={{ fontSize: "var(--text-xl)", color: Number(p.amount_confirmed) > 0 ? "var(--success)" : undefined }}>
                    {p.amount_confirmed} <small>{p.symbol}</small></span></div>
              </div>
              <div className="stack--sm"><span className="label">Deposit address</span><CopyField value={p.address} /></div>
              <Kv items={[
                ["Asset id", <span className="mono xs break">{p.asset}</span>],
                ["Seen (incl. unconfirmed)", <span className="num">{p.amount_seen} {p.symbol}</span>],
                ["Required confirmations", <span className="num">{p.confirmations_required}</span>],
                ...(p.rate ? [["Quoted rate", <span className="num">{p.rate} {i.price.currency} / {p.symbol} · {p.rate_source}</span>] as [string, JSX.Element]] : []),
                ...(p.explorer_url ? [["Explorer", <a href={p.explorer_url} target="_blank" rel="noreferrer noopener">View address ↗</a>] as [string, JSX.Element]] : []),
              ]} />
            </div>
          ) : <p className="muted small">The payer has not selected an asset yet. No address has been allocated.</p>}
        </Panel>

        <Panel title="Details">
          <Kv items={[
            ["Price", <span className="num">{isFiat(i.price.currency) ? money(i.price.amount, i.price.currency) : `${i.price.amount} (${i.price.currency})`}</span>],
            ["Order reference", i.order_id ? <span className="mono">{i.order_id}</span> : "—"],
            ["Description", i.description ?? "—"],
            ["Created", dateTime(i.created_at)],
            ["Expires", <>{dateTime(i.expires_at)} <span className="muted xs">({relative(i.expires_at)})</span></>],
            ["Paid", dateTime(i.paid_at)],
            ["Checkout URL", <CopyInline value={i.checkout_url} display={shortHash(i.checkout_url, 24, 8)} />],
            ["Invoice id", <CopyInline value={i.id} />],
          ]} />
          {Object.keys(i.metadata ?? {}).length > 0 && (
            <div className="stack--sm" style={{ marginTop: "var(--s-4)" }}>
              <span className="label">Metadata</span><pre className="code">{JSON.stringify(i.metadata, null, 2)}</pre></div>
          )}
        </Panel>
      </div>

      <Panel title="On-chain transfers" flush>
        {i.payments.length === 0 ? <p className="panel__body muted small">No transfers detected yet.</p> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Transaction</th><th>Status</th><th className="r">Amount</th><th>Confirmations</th><th>Source</th><th className="r">Seen</th></tr></thead>
            <tbody>{i.payments.map((x) => (
              <tr key={x.id}>
                <td className="mono small">{x.explorer_url ? <a href={x.explorer_url} target="_blank" rel="noreferrer noopener">{shortHash(x.txid, 10, 6)} ↗</a> : shortHash(x.txid, 14, 6)}</td>
                <td><Status value={x.status} /></td>
                <td className="r num">{x.amount} {x.symbol}</td>
                <td style={{ minWidth: 160 }}>{x.status === "orphaned" || x.status === "rejected" ? <span className="xs muted">—</span> : <Confirmations have={x.confirmations} need={x.required} />}</td>
                <td className="xs muted">{x.source.replace("_", " ")}</td>
                <td className="r xs muted nowrap">{relative(x.first_seen_at)}</td>
              </tr>))}</tbody></table></div>
        )}
      </Panel>

      <div className="grid-2" style={{ alignItems: "start" }}>
        <Panel title="Event timeline" flush>
          <table className="table"><tbody>{(i.events ?? []).map((e) => (
            <tr key={e.id}><td className="mono small">{e.type}</td><td className="r xs muted nowrap">{dateTime(e.created_at)}</td></tr>))}</tbody></table>
        </Panel>
        <Panel title="Quotes & derivation" eyebrow="Audit trail" flush>
          <table className="table"><tbody>{i.quotes.map((qq) => {
            const d = i.deposit_details?.find((x) => x.id === qq.id);
            return (
              <tr key={qq.id}>
                <td><div><span className="small">{qq.amount_due} {qq.symbol}</span>
                  <span className="xs mono faint">{shortHash(qq.address, 10, 8)}</span></div></td>
                <td className="xs mono muted">{d?.path} · #{d?.index}</td>
                <td className="r">{d?.swept_at ? <Status value="confirmed" /> : qq.id === p?.id ? <span className="tag">active</span> : null}</td>
              </tr>);
          })}</tbody></table>
        </Panel>
      </div>

      <Dialog open={confirmCancel} onClose={() => setConfirmCancel(false)} title={`Cancel ${i.number}?`}
        description="The checkout will tell the payer not to send funds. The address stays monitored; anything that still arrives is flagged for review."
        footer={<><Button variant="ghost" onClick={() => setConfirmCancel(false)}>Keep invoice</Button>
          <Button variant="danger" onClick={cancel} loading={busy}>Cancel invoice</Button></>} />
      <Dialog open={resolving} onClose={() => setResolving(false)} title="Resolve review"
        footer={<><Button variant="ghost" onClick={() => setResolving(false)}>Close</Button>
          <Button variant="primary" onClick={resolve} loading={busy} disabled={note.length < 3}>Mark resolved</Button></>}>
        <Textarea label="Resolution note" hint="Recorded in the audit log." value={note} onChange={(e) => setNote(e.target.value)} />
      </Dialog>
    </>
  );
}
