import { useState } from "react";
import { Link } from "react-router-dom";
import { Confirmations, Empty, ErrorBlock, LoadingBlock, Panel, Segmented, Status } from "../components/ui";
import { relative, shortHash } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { Paged } from "../lib/types";

interface Row {
  id: string; txid: string; network: string; network_name: string; symbol: string; amount: string; status: string;
  confirmations: number; required: number; source: string; invoice_id: string; invoice_number: string | null;
  first_seen_at: string; explorer_url: string | null;
}

export function Payments() {
  const [status, setStatus] = useState<"" | "pending" | "confirmed" | "orphaned" | "rejected">("");
  const list = useApi<Paged<Row>>(`/api/admin/payments?limit=100${status ? `&status=${status}` : ""}`, 10000);
  return (
    <>
      <div className="filters">
        <Segmented label="Status" value={status} onChange={setStatus} options={[
          { value: "", label: "All" }, { value: "pending", label: "Confirming" }, { value: "confirmed", label: "Confirmed" },
          { value: "orphaned", label: "Orphaned" }, { value: "rejected", label: "Rejected" }]} />
        <span className="xs faint">Every transfer CRYPTUS attributed to an invoice address, re-verified against the chain.</span>
      </div>
      <Panel flush>
        <ErrorBlock error={list.error} retry={list.reload} />
        {!list.data ? <LoadingBlock /> : list.data.items.length === 0 ? (
          <Empty icon="payments" title="No transfers">Transfers appear as soon as they reach the mempool or a block.</Empty>
        ) : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Transaction</th><th>Network</th><th className="r">Amount</th><th>Status</th><th>Confirmations</th><th>Invoice</th><th className="r">Seen</th></tr></thead>
            <tbody>{list.data.items.map((p) => (
              <tr key={p.id}>
                <td className="mono small nowrap">{p.explorer_url ? <a href={p.explorer_url} target="_blank" rel="noreferrer noopener">{shortHash(p.txid, 10, 6)} ↗</a>
                  : <span title={p.txid}>{shortHash(p.txid, 14, 4)}</span>}</td>
                <td className="small nowrap">{p.network_name}</td>
                <td className="r num nowrap">{p.amount} {p.symbol}</td>
                <td><Status value={p.status} />{p.source !== "scan" && <span className="tag" style={{ marginLeft: 6 }}>{p.source.replace("_", " ")}</span>}</td>
                <td style={{ minWidth: 150 }}>{["orphaned", "rejected"].includes(p.status) ? "—" : <Confirmations have={p.confirmations} need={p.required} />}</td>
                <td><Link to={`/invoices/${p.invoice_id}`} className="mono small">{p.invoice_number}</Link></td>
                <td className="r xs muted nowrap">{relative(p.first_seen_at)}</td>
              </tr>))}</tbody></table></div>
        )}
      </Panel>
    </>
  );
}
