import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Button, Dialog, Empty, ErrorBlock, Input, LoadingBlock, Panel, Segmented, Select, Status, useToast } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, isFiat, money, relative } from "../lib/format";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import type { AssetRow, InvoiceFull, InvoiceRow, Paged } from "../lib/types";

const FILTERS = [
  { value: "", label: "All" }, { value: "pending,processing", label: "Open" }, { value: "paid", label: "Paid" },
  { value: "expired,underpaid", label: "Unpaid" }, { value: "review", label: "Needs review" },
] as const;
type F = (typeof FILTERS)[number]["value"];
const PAGE = 50;

export function Invoices() {
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const { can } = useSession();
  const [filter, setFilter] = useState<F>(params.get("review") ? "review" : "");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(params.get("new") === "1");
  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (filter === "review") qs.set("review", "true"); else if (filter) qs.set("status", filter);
  if (query) qs.set("q", query);
  const list = useApi<Paged<InvoiceRow>>(`/api/admin/invoices?${qs}`, 15000);

  useEffect(() => {
    const t = window.setTimeout(() => { setQuery(q.trim()); setOffset(0); }, 300);
    return () => window.clearTimeout(t);
  }, [q]);

  return (
    <>
      <div className="filters">
        <Segmented label="Status" value={filter} onChange={(v) => { setFilter(v); setOffset(0); }}
          options={FILTERS.map((f) => ({ value: f.value, label: f.label }))} />
        <div className="grow" style={{ minWidth: 200, maxWidth: 360 }}>
          <input className="input" placeholder="Search number, order id, address or txid" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search invoices" />
        </div>
        <div className="row" style={{ marginLeft: "auto" }}>
          {can("operator") && <a className="btn btn--ghost btn--sm" href="/api/admin/invoices/export.csv">Export CSV</a>}
          {can("operator") && <Button variant="primary" size="sm" icon="plus" onClick={() => setCreating(true)}>New invoice</Button>}
        </div>
      </div>

      <Panel flush>
        <ErrorBlock error={list.error} retry={list.reload} />
        {!list.data ? <LoadingBlock /> : list.data.items.length === 0 ? (
          <Empty icon="invoice" title="No invoices match">Invoices created by your store through the API appear here in real time.</Empty>
        ) : (
          <div className="table-wrap" style={{ opacity: list.loading ? 0.7 : 1 }}>
            <table className="table">
              <thead><tr><th>Invoice</th><th>Status</th><th className="r">Price</th><th className="r">Due</th><th>Network</th>
                <th>Order</th><th className="r">Created</th></tr></thead>
              <tbody>
                {list.data.items.map((i) => (
                  <tr key={i.id} data-href onClick={() => nav(`/invoices/${i.id}`)}>
                    <td className="nowrap">
                      <Link to={`/invoices/${i.id}`} className="mono" style={{ color: "var(--fg)" }}>{i.number}</Link>
                      {i.mode === "test" && <span className="tag" style={{ marginLeft: 8 }}>test</span>}
                    </td>
                    <td className="nowrap"><Status value={i.status} />
                      {i.flags.needs_review && <span className="badge badge--accent" style={{ marginLeft: 6 }}>review</span>}
                      {i.flags.overpaid && <span className="badge badge--info" style={{ marginLeft: 6 }}>overpaid</span>}</td>
                    <td className="r num nowrap">{isFiat(i.price.currency) ? money(i.price.amount, i.price.currency) : i.price.amount}</td>
                    <td className="r num nowrap">{i.amount_due ? `${i.amount_due} ${i.asset}` : <span className="faint">—</span>}</td>
                    <td className="small nowrap">{i.network ?? <span className="faint">payer choice</span>}</td>
                    <td className="small mono truncate" style={{ maxWidth: 160 }}>{i.order_id ?? ""}</td>
                    <td className="r xs muted nowrap" title={dateTime(i.created_at)}>{relative(i.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {list.data && list.data.total > PAGE && (
          <div className="panel__foot" style={{ justifyContent: "space-between" }}>
            <span className="xs muted num">{offset + 1}–{Math.min(offset + PAGE, list.data.total)} of {list.data.total}</span>
            <div className="row">
              <Button size="sm" variant="ghost" icon="arrowLeft" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>Previous</Button>
              <Button size="sm" variant="ghost" iconRight="arrowRight" disabled={offset + PAGE >= list.data.total}
                onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </div>
          </div>
        )}
      </Panel>

      <CreateInvoice open={creating} onClose={() => { setCreating(false); if (params.get("new")) setParams({}); }}
        onCreated={(inv) => nav(`/invoices/${inv.id}`)} />
    </>
  );
}

function CreateInvoice({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (i: InvoiceFull) => void }) {
  const toast = useToast();
  const assets = useApi<AssetRow[]>(open ? "/api/admin/assets" : null);
  const settings = useApi<{ invoice: { default_currency: string } }>(open ? "/api/admin/settings" : null);
  const [mode, setMode] = useState<"live" | "test">("live");
  const [pricing, setPricing] = useState<"fiat" | "crypto">("fiat");
  const [amount, setAmount] = useState("");
  const [asset, setAsset] = useState("");
  const [description, setDescription] = useState("");
  const [orderId, setOrderId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const env = mode === "live" ? "mainnet" : "testnet";
  const usable = (assets.data ?? []).filter((a) => a.enabled && a.environment === env);
  const fiat = settings.data?.invoice.default_currency ?? "USD";

  async function submit() {
    setPending(true);
    setError(null);
    try {
      const inv = await api.post<InvoiceFull>("/api/admin/invoices", {
        mode, amount, currency: pricing === "fiat" ? fiat : asset, asset: pricing === "crypto" ? asset : (asset || null),
        description: description || null, order_id: orderId || null,
      });
      toast("success", `Invoice ${inv.number} created`);
      onCreated(inv);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="New invoice"
      description="Each invoice gets its own deposit address. Share the checkout link with your customer."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={submit} loading={pending} disabled={!amount || (pricing === "crypto" && !asset)}>Create invoice</Button></>}>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="row">
        <Segmented label="Mode" value={mode} onChange={(v) => { setMode(v); setAsset(""); }}
          options={[{ value: "live", label: "Live" }, { value: "test", label: "Test (testnets)" }]} />
        <Segmented label="Pricing" value={pricing} onChange={setPricing}
          options={[{ value: "fiat", label: `Priced in ${fiat}` }, { value: "crypto", label: "Priced in crypto" }]} />
      </div>
      <div className="grid-2">
        <Input label="Amount" inputMode="decimal" mono value={amount} onChange={(e) => setAmount(e.target.value)}
          suffix={pricing === "fiat" ? fiat : usable.find((a) => a.id === asset)?.symbol ?? "—"} autoFocus />
        <Select label={pricing === "fiat" ? "Payment asset" : "Asset"} value={asset} onChange={(e) => setAsset(e.target.value)}
          hint={pricing === "fiat" && !asset ? "Leave open to let the payer choose." : undefined}>
          <option value="">{pricing === "fiat" ? "Payer chooses" : "Select…"}</option>
          {usable.map((a) => <option key={a.id} value={a.id}>{a.symbol} · {a.network_name}</option>)}
        </Select>
        <Input label="Description" value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} placeholder="Shown to the payer" />
        <Input label="Order reference" mono value={orderId} maxLength={128} onChange={(e) => setOrderId(e.target.value)} />
      </div>
    </Dialog>
  );
}
