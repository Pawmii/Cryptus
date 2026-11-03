import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Lockup, Mark } from "../components/Logo";
import { Alert, Button, Check, Confirmations, CopyField, LoadingBlock, Status, useCopy } from "../components/ui";
import { api } from "../lib/api";
import { assetGlyph, countdown, isFiat, money, shortHash } from "../lib/format";
import { useNow } from "../lib/hooks";
import type { Quote } from "../lib/types";

interface PublicInvoice {
  id: string; number: string; status: string; mode: string; price: { amount: string; currency: string };
  description: string | null; expires_at: string; server_time: string; redirect_url: string | null;
  flags: { overpaid: boolean; paid_late: boolean };
  merchant: { name: string; website: string | null; support_email: string | null };
  compliance: { terms_url: string; privacy_url: string; refund_policy_url: string; checkout_notice: string; require_terms_acceptance: boolean };
  options: { asset: string; symbol: string; name: string; kind: string; network: string; network_name: string; environment: string }[];
  payment: (Quote & { qr_svg: string }) | null;
  payments: { txid: string; amount: string; symbol: string; status: string; confirmations: number; required: number; explorer_url: string | null }[];
}

export function Checkout() {
  const { id = "" } = useParams();
  const [inv, setInv] = useState<PublicInvoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState<string | null>(null);
  const [changing, setChanging] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const now = useNow(1000);
  const [skew, setSkew] = useState(0);

  async function load() {
    try {
      const d = await api.get<PublicInvoice>(`/api/public/invoices/${id}`);
      setSkew(new Date(d.server_time).getTime() - Date.now());
      setInv(d);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  useEffect(() => {
    if (!inv || ["paid", "cancelled"].includes(inv.status)) return;
    const t = window.setInterval(() => document.visibilityState === "visible" && load(), inv.status === "processing" ? 4000 : 7000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inv?.status]);

  useEffect(() => {
    document.title = inv ? `Pay ${inv.merchant.name} · ${inv.number}` : "CRYPTUS Checkout";
  }, [inv]);

  async function select(asset: string) {
    setSelecting(asset);
    setError(null);
    try {
      setInv(await api.post<PublicInvoice>(`/api/public/invoices/${id}/select`, { asset }));
      setChanging(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSelecting(null);
    }
  }

  const remaining = inv ? new Date(inv.expires_at).getTime() - (now + skew) : 0;
  const groups = useMemo(() => {
    const g: Record<string, PublicInvoice["options"]> = {};
    inv?.options.forEach((o) => (g[o.network_name] ??= []).push(o));
    return Object.entries(g);
  }, [inv]);

  if (!inv) {
    return (
      <div className="checkout">
        <div className="checkout__bar"><Lockup size={22} /></div>
        <div className="checkout__body">{error ? <Alert tone="danger" title="Invoice unavailable">{error}</Alert> : <LoadingBlock />}</div>
      </div>
    );
  }

  const p = inv.payment;
  const seen = inv.payments.filter((x) => !["orphaned", "rejected"].includes(x.status));
  const showOptions = (!p || changing) && ["pending"].includes(inv.status);
  const needsTerms = inv.compliance.require_terms_acceptance && !accepted && !p;

  return (
    <div className="checkout">
      <header className="checkout__bar">
        <div className="row">
          <strong style={{ fontWeight: 640 }}>{inv.merchant.name}</strong>
          {inv.mode === "test" && <Status value="test" />}
        </div>
        <span className="checkout__secured xs muted"><Icon name="lock" size={14} /><span className="checkout__secured-text">Secured by</span><Mark size={16} /><span className="wordmark xs">CRYPTUS</span></span>
      </header>

      <main className="checkout__body">
        <div className="panel panel--accent checkout__card">
          <section className="checkout__summary" aria-label="Order summary">
            <div className="stack--sm">
              <span className="label">Amount due</span>
              <div className="checkout__amount">
                {isFiat(inv.price.currency) ? money(inv.price.amount, inv.price.currency)
                  : <>{inv.price.amount}<small>{p?.symbol ?? ""}</small></>}
              </div>
              {inv.description && <p className="secondary">{inv.description}</p>}
            </div>
            <dl className="kv">
              <dt>Invoice</dt><dd className="mono">{inv.number}</dd>
              <dt>Status</dt><dd><Status value={inv.status} live={inv.status === "processing"} /></dd>
              {["pending", "processing"].includes(inv.status) && <>
                <dt>Expires in</dt>
                <dd className="countdown" style={{ color: remaining < 120000 ? "var(--warning)" : undefined }}>{countdown(remaining)}</dd>
              </>}
              {p?.rate && isFiat(inv.price.currency) && <>
                <dt>Locked rate</dt><dd className="num small">1 {p.symbol} = {money(Number(p.rate).toFixed(2), inv.price.currency)}</dd>
              </>}
            </dl>
            {inv.compliance.checkout_notice && <p className="xs muted">{inv.compliance.checkout_notice}</p>}
            <p className="xs faint">No account, no tracking, no personal data. This page talks only to {inv.merchant.name}'s own server.</p>
          </section>

          <section className="checkout__pay" aria-live="polite">
            {error && <Alert tone="danger">{error}</Alert>}

            {inv.status === "paid" ? <Paid inv={inv} /> :
              ["expired", "underpaid", "cancelled"].includes(inv.status) ? <Closed inv={inv} /> :
                showOptions ? (
                  <div className="stack">
                    <div className="stack--sm">
                      <span className="label">Step 1</span>
                      <h2>Choose how to pay</h2>
                    </div>
                    {inv.compliance.require_terms_acceptance && !p && (
                      <Check checked={accepted} onChange={(e) => setAccepted(e.target.checked)} label={<>I accept the{" "}
                        <a href={inv.compliance.terms_url} target="_blank" rel="noreferrer noopener">terms</a>
                        {inv.compliance.refund_policy_url && <> and <a href={inv.compliance.refund_policy_url} target="_blank" rel="noreferrer noopener">refund policy</a></>}.</>} />
                    )}
                    {groups.map(([net, opts]) => (
                      <div key={net} className="stack--sm">
                        <span className="label">{net}</span>
                        {opts.map((o) => (
                          <button key={o.asset} className="asset-option" aria-pressed={p?.asset === o.asset}
                            disabled={!!selecting || needsTerms} onClick={() => select(o.asset)}>
                            <span className="asset-glyph">{assetGlyph(o.symbol)}</span>
                            <span className="grow vstack">
                              <strong style={{ fontWeight: 600 }}>{o.symbol}</strong>
                              <span className="xs muted">{o.kind === "native" ? `Native ${o.network_name} coin` : o.kind === "jetton" ? "Jetton on TON" : `Token on ${o.network_name}`}</span>
                            </span>
                            {selecting === o.asset ? <span className="spinner" /> : <Icon name="chevron" size={14} />}
                          </button>
                        ))}
                      </div>
                    ))}
                    {changing && <Button variant="ghost" size="sm" icon="arrowLeft" onClick={() => setChanging(false)}>Keep {p?.symbol}</Button>}
                  </div>
                ) : p && (
                  <div className="stack">
                    <div className="row row--between">
                      <div className="stack--sm">
                        <span className="label">Step 2 · Send exactly</span>
                        <AmountCopy amount={p.amount_due} symbol={p.symbol} />
                      </div>
                      {inv.status === "pending" && seen.length === 0 && inv.options.length > 1 &&
                        <Button size="sm" variant="ghost" onClick={() => setChanging(true)}>Change</Button>}
                    </div>
                    <Alert tone="warning" title={`Send only ${p.symbol} on ${p.network_name}`}>
                      {p.asset_kind === "jetton" ? <>USDT Jetton, master <span className="mono">{shortHash(p.contract, 6, 6)}</span>. </> :
                        p.asset_kind === "erc20" ? <>Token contract <span className="mono">{shortHash(p.contract, 6, 4)}</span>. </> : null}
                      Assets sent on another network or as another token may be lost.
                      {p.network.startsWith("ton") && !p.memo && " No memo or comment is needed — this address is unique to this invoice."}
                    </Alert>
                    {p.memo && (
                      <Alert tone="danger" title="Add the comment below to your transfer">
                        This address belongs to {inv.merchant.name}; the comment is how your payment is matched to this invoice.
                        Payments without it, or with an encrypted comment, are not credited automatically.
                      </Alert>
                    )}
                    <div className="row" style={{ alignItems: "flex-start", gap: "var(--s-5)" }}>
                      <div className="qr-plate" dangerouslySetInnerHTML={{ __html: p.qr_svg }} role="img" aria-label="Payment QR code" />
                      <div className="grow stack" style={{ minWidth: 220 }}>
                        <div className="stack--sm">
                          <span className="label">{p.memo ? "Address" : "Deposit address"}</span>
                          <CopyField value={p.address} label="address" />
                        </div>
                        {p.memo && (
                          <div className="stack--sm">
                            <span className="label">Comment (required)</span>
                            <CopyField value={p.memo} label="comment" />
                          </div>
                        )}
                        {p.payment_uri && <a className="btn btn--secondary btn--sm" href={p.payment_uri}><Icon name="external" size={14} />Open in wallet</a>}
                        {p.memo && <span className="xs muted">The QR code and button fill in the comment for you.</span>}
                      </div>
                    </div>
                    {seen.length > 0 ? (
                      <div className="panel panel--sm panel--raised" style={{ padding: "var(--s-4)" }}>
                        <div className="stack">
                          <div className="row row--between">
                            <span className="row small"><span className="spinner" /> Payment detected — awaiting confirmations</span>
                            <span className="num small">{p.amount_seen} / {p.amount_due} {p.symbol}</span>
                          </div>
                          {seen.map((x) => (
                            <div key={x.txid} className="stack--sm">
                              <Confirmations have={x.confirmations} need={x.required} />
                              {x.explorer_url && <a className="xs mono" href={x.explorer_url} target="_blank" rel="noreferrer noopener">{shortHash(x.txid, 10, 8)} ↗</a>}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="stack--sm">
                        <div className="loading-bar" />
                        <span className="xs muted">Watching the {p.network_name} chain for your transaction. This page updates automatically.</span>
                      </div>
                    )}
                  </div>
                )}
          </section>
        </div>
      </main>

      <footer className="checkout__foot">
        {inv.merchant.support_email && <a href={`mailto:${inv.merchant.support_email}`}>Support</a>}
        {inv.compliance.terms_url && <a href={inv.compliance.terms_url} target="_blank" rel="noreferrer noopener">Terms</a>}
        {inv.compliance.privacy_url && <a href={inv.compliance.privacy_url} target="_blank" rel="noreferrer noopener">Privacy</a>}
        <span>Private Payments. Open Infrastructure.</span>
      </footer>
    </div>
  );
}

function AmountCopy({ amount, symbol }: { amount: string; symbol: string }) {
  const { copied, copy } = useCopy();
  return (
    <button className="row row--nowrap" onClick={() => copy(amount)} title="Copy amount"
      style={{ background: "none", border: 0, padding: 0, cursor: "pointer", textAlign: "left" }}>
      <span className="checkout__amount" style={{ fontSize: "var(--text-2xl)" }}>{amount}<small>{symbol}</small></span>
      <Icon name={copied ? "check" : "copy"} size={16} style={{ color: copied ? "var(--success)" : "var(--fg-muted)" }} />
    </button>
  );
}

function Paid({ inv }: { inv: PublicInvoice }) {
  return (
    <div className="stack" style={{ justifyItems: "start" }}>
      <span className="result-mark result-mark--success"><Icon name="check" size={28} /></span>
      <div className="stack--sm">
        <h2>Payment confirmed</h2>
        <p className="muted">{inv.merchant.name} has received your payment{inv.flags.paid_late ? " (after the quote window)" : ""}.
          {inv.flags.overpaid && " You sent more than required — contact the merchant about the difference."}</p>
      </div>
      {inv.payments.filter((p) => p.status === "confirmed").map((p) => (
        <div key={p.txid} className="row small">
          <span className="num">{p.amount} {p.symbol}</span>
          {p.explorer_url && <a className="mono xs" href={p.explorer_url} target="_blank" rel="noreferrer noopener">{shortHash(p.txid)} ↗</a>}
        </div>
      ))}
      {inv.redirect_url && <a className="btn btn--primary" href={inv.redirect_url}>Return to {inv.merchant.name}<Icon name="arrowRight" size={16} /></a>}
    </div>
  );
}

function Closed({ inv }: { inv: PublicInvoice }) {
  const msg = inv.status === "underpaid"
    ? "A partial payment was received before this invoice expired. Contact the merchant to resolve the balance."
    : inv.status === "cancelled" ? "The merchant cancelled this invoice. Do not send funds to its address."
      : "The payment window has closed. Do not send funds to this address — request a new invoice from the merchant.";
  return (
    <div className="stack" style={{ justifyItems: "start" }}>
      <span className="result-mark result-mark--muted"><Icon name={inv.status === "cancelled" ? "x" : "clock"} size={26} /></span>
      <div className="stack--sm">
        <h2>{inv.status === "underpaid" ? "Partially paid" : inv.status === "cancelled" ? "Invoice cancelled" : "Invoice expired"}</h2>
        <p className="muted">{msg}</p>
      </div>
      {inv.merchant.support_email && <a className="btn btn--secondary" href={`mailto:${inv.merchant.support_email}?subject=${inv.number}`}>Contact support</a>}
    </div>
  );
}
