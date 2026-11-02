import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { BarChart } from "../components/BarChart";
import { Icon } from "../components/Icon";
import { Alert, Button, Empty, ErrorBlock, LoadingBlock, Panel, Segmented, Status } from "../components/ui";
import { compact, isFiat, money, relative } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { InvoiceRow } from "../lib/types";

interface OverviewData {
  currency: string; days: number;
  kpis: { volume: string; paid: number; created: number; conversion: number | null; open: number; needs_review: number; sweeps_pending: number };
  series: { date: string; volume: string; paid: number; created: number }[];
  received: { asset: string; symbol: string; network: string; amount: string; count: number }[];
  networks: { key: string; name: string; environment: string; status: string; health: { height?: number; latency_ms?: number } }[];
  recent: InvoiceRow[];
}

export function Overview() {
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const { data, error, loading, reload } = useApi<OverviewData>(`/api/admin/overview?days=${days}`, 20000);
  const [view, setView] = useState<"chart" | "table">("chart");
  const nav = useNavigate();
  if (!data) return error ? <ErrorBlock error={error} retry={reload} /> : <LoadingBlock />;
  const k = data.kpis;
  const live = data.networks.filter((n) => n.status === "operational");
  const fmt = (v: number) => (isFiat(data.currency) ? money(String(v.toFixed(2)), data.currency).replace(/\.00$/, "") : compact(v));

  return (
    <div className="stack--lg" style={{ display: "grid", opacity: loading ? 0.85 : 1, transition: "opacity var(--dur-base)" }}>
      <div className="filters">
        <Segmented label="Period" value={days} onChange={setDays}
          options={[{ value: "7", label: "7 days" }, { value: "30", label: "30 days" }, { value: "90", label: "90 days" }]} />
        <span className="xs faint">Live mode · {data.currency}-priced invoices</span>
      </div>

      {live.length === 0 && (
        <Alert tone="warning" title="No operational mainnet network">
          Payments cannot be accepted in live mode yet. <Link to="/networks">Configure a network</Link> and{" "}
          <Link to="/wallets">attach a verified wallet</Link>.
        </Alert>
      )}
      {k.needs_review > 0 && (
        <Alert tone="accent" title={`${k.needs_review} invoice${k.needs_review > 1 ? "s" : ""} need review`}>
          Funds arrived in a way that needs an operator decision. <Link to="/invoices?review=1">Review now</Link>
        </Alert>
      )}

      <div className="grid-4">
        <div className="panel panel--accent"><div className="stat">
          <span className="label">Settled volume</span>
          <span className="stat__value">{money(k.volume, data.currency)}</span>
          <span className="stat__meta">{k.paid} paid invoices · {data.days}d</span>
        </div></div>
        <div className="panel"><div className="stat">
          <span className="label">Conversion</span>
          <span className="stat__value">{k.conversion === null ? "—" : `${(k.conversion * 100).toFixed(1)}%`}</span>
          <span className="stat__meta">paid ÷ closed invoices</span>
        </div></div>
        <div className="panel"><div className="stat">
          <span className="label">Open invoices</span>
          <span className="stat__value">{k.open}</span>
          <span className="stat__meta">{k.created} created in period</span>
        </div></div>
        <div className="panel"><div className="stat">
          <span className="label">Treasury queue</span>
          <span className="stat__value">{k.sweeps_pending}</span>
          <span className="stat__meta"><Link to="/consolidation">sweeps awaiting action</Link></span>
        </div></div>
      </div>

      <div className="grid-wide">
        <Panel title="Settled volume per day" eyebrow={data.currency}
          actions={<Segmented label="View" value={view} onChange={setView}
            options={[{ value: "chart", label: "Chart" }, { value: "table", label: "Table" }]} />}>
          {view === "chart" && data.series.every((x) => Number(x.volume) === 0) ? (
            <Empty icon="pulse" title="No settled volume in this period">Paid {data.currency}-priced invoices will chart here.</Empty>
          ) : view === "chart" ? (
            <BarChart ariaLabel={`Settled volume per day in ${data.currency}`} format={fmt}
              data={data.series.map((s) => ({
                key: s.date, value: Number(s.volume),
                label: new Date(s.date + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }),
                detail: `${s.paid} paid`,
              }))} />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 220 }}>
              <table className="table"><thead><tr><th>Date</th><th className="r">Volume</th><th className="r">Paid</th><th className="r">Created</th></tr></thead>
                <tbody>{[...data.series].reverse().map((s) => (
                  <tr key={s.date}><td className="mono">{s.date}</td><td className="r num">{money(s.volume, data.currency)}</td>
                    <td className="r num">{s.paid}</td><td className="r num">{s.created}</td></tr>))}</tbody></table>
            </div>
          )}
        </Panel>
        <Panel title="Networks" actions={<Button size="sm" variant="ghost" iconRight="chevron" onClick={() => nav("/networks")}>All</Button>} flush>
          {data.networks.filter((n) => n.status !== "not_configured").length === 0 ? (
            <Empty icon="networks" title="Nothing connected">Connect a node or API endpoint to start monitoring.</Empty>
          ) : (
            <table className="table"><tbody>
              {data.networks.filter((n) => n.status !== "not_configured").map((n) => (
                <tr key={n.key}>
                  <td><div className="vstack"><span className="nowrap">{n.name}</span>
                    <span className="xs faint num">{n.environment}{n.health?.height ? ` · #${n.health.height.toLocaleString()}` : ""}</span></div></td>
                  <td className="r"><Status value={n.status} /></td>
                </tr>))}
            </tbody></table>
          )}
        </Panel>
      </div>

      <div className="grid-wide">
        <Panel title="Recent invoices" flush actions={<Button size="sm" variant="ghost" iconRight="chevron" onClick={() => nav("/invoices")}>All invoices</Button>}>
          {data.recent.length === 0 ? (
            <Empty icon="invoice" title="No invoices yet">Create one here or through the merchant API.</Empty>
          ) : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Invoice</th><th>Status</th><th className="r">Price</th><th>Asset</th><th className="r">Created</th></tr></thead>
              <tbody>{data.recent.map((i) => (
                <tr key={i.id} data-href onClick={() => nav(`/invoices/${i.id}`)}>
                  <td><Link to={`/invoices/${i.id}`} className="mono" style={{ color: "var(--fg)" }}>{i.number}</Link>
                    {i.mode === "test" && <span className="tag" style={{ marginLeft: 8 }}>test</span>}</td>
                  <td><Status value={i.status} /></td>
                  <td className="r num">{money(i.price.amount, isFiat(i.price.currency) ? i.price.currency : "")}</td>
                  <td className="small">{i.asset ? `${i.asset} · ${i.network}` : <span className="faint">not selected</span>}</td>
                  <td className="r xs muted nowrap">{relative(i.created_at)}</td>
                </tr>))}
              </tbody></table></div>
          )}
        </Panel>
        <Panel title="Received on-chain" eyebrow={`Confirmed · ${data.days}d`} flush>
          {data.received.length === 0 ? <Empty icon="asset" title="No confirmed payments" /> : (
            <table className="table"><tbody>{data.received.map((r) => (
              <tr key={r.asset}>
                <td><div className="vstack"><strong style={{ fontWeight: 600 }}>{r.symbol}</strong><span className="xs faint">{r.network}</span></div></td>
                <td className="r num">{r.amount}</td>
                <td className="r xs muted">{r.count}×</td>
              </tr>))}</tbody></table>
          )}
        </Panel>
      </div>
      <p className="xs faint row"><Icon name="info" size={13} />Figures update every 20 seconds. Testnet activity is excluded.</p>
    </div>
  );
}
