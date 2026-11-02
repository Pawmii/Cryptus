import { useEffect, useState } from "react";
import { Alert, Button, Check, ErrorBlock, Input, LoadingBlock, PageHead, Panel, Select, Tabs, Textarea, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

type S = Record<string, Record<string, unknown>>;
const CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "SGD", "AED", "PLN", "TRY", "BRL", "INR", "UAH", "KZT"];

export function Settings() {
  const s = useApi<S>("/api/admin/settings");
  const [tab, setTab] = useState<"business" | "invoice" | "compliance" | "security" | "rates">("business");
  if (!s.data) return s.error ? <ErrorBlock error={s.error} /> : <LoadingBlock />;
  return (
    <>
      <PageHead title="Settings">Operational configuration. Every change is recorded in the audit log.</PageHead>
      <Tabs value={tab} onChange={setTab} options={[
        { value: "business", label: "Business" }, { value: "invoice", label: "Invoices" }, { value: "compliance", label: "Policies & compliance" },
        { value: "security", label: "Security" }, { value: "rates", label: "Exchange rates" }]} />
      <Section key={tab} section={tab} initial={s.data[tab]} onSaved={s.reload} />
    </>
  );
}

function Section({ section, initial, onSaved }: { section: string; initial: Record<string, unknown>; onSaved: () => void }) {
  const toast = useToast();
  const { can } = useSession();
  const [v, setV] = useState<Record<string, unknown>>(initial);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setV(initial), [initial]);
  const set = (k: string) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  const str = (k: string) => (v[k] ?? "") as string;
  const editable = section === "security" ? can("owner") : can("admin");

  async function save() {
    setPending(true);
    setError(null);
    try {
      const body = { ...v };
      delete body.has_api_key;
      await api.put(`/api/admin/settings/${section}`, body);
      toast("success", "Settings saved");
      onSaved();
    } catch (e) { setError((e as Error).message); } finally { setPending(false); }
  }

  return (
    <Panel footer={editable && <Button variant="primary" onClick={save} loading={pending}>Save changes</Button>}>
      <div className="stack">
        {error && <Alert tone="danger">{error}</Alert>}
        {section === "business" && <div className="grid-2">
          <Input label="Business name" value={str("name")} onChange={set("name")} disabled={!editable} />
          <Input label="Website" value={str("website")} onChange={set("website")} disabled={!editable} />
          <Input label="Support email" value={str("support_email")} onChange={set("support_email")} disabled={!editable} />
          <Input label="Statement descriptor" value={str("statement_descriptor")} onChange={set("statement_descriptor")} disabled={!editable} />
        </div>}
        {section === "invoice" && <div className="grid-2">
          <Select label="Default pricing currency" value={str("default_currency")} onChange={set("default_currency")} disabled={!editable}>
            {CURRENCIES.map((c) => <option key={c}>{c}</option>)}</Select>
          <Input label="Default lifetime" type="number" suffix="minutes" value={str("default_expiry_minutes")}
            onChange={(e) => setV({ ...v, default_expiry_minutes: Number(e.target.value) })} disabled={!editable} />
          <Input label="Late-payment window" type="number" suffix="minutes" value={str("late_payment_window_minutes")}
            onChange={(e) => setV({ ...v, late_payment_window_minutes: Number(e.target.value) })} disabled={!editable}
            hint="Addresses stay monitored this long after expiry so late payments are never lost." />
          <Input label="Underpayment tolerance" type="number" suffix="bps" value={str("underpay_tolerance_bps")}
            onChange={(e) => setV({ ...v, underpay_tolerance_bps: Number(e.target.value) })} disabled={!editable}
            hint="100 bps = 1%. Keep at 0 unless you accept short payments." />
          <Input label="Quote precision" type="number" suffix="decimals" value={str("amount_display_decimals")}
            onChange={(e) => setV({ ...v, amount_display_decimals: Number(e.target.value) })} disabled={!editable}
            hint="Quotes are always rounded up, never down." />
        </div>}
        {section === "compliance" && <>
          <Alert tone="info" title="Self-hosting does not remove legal obligations">
            You remain responsible for licensing, tax, sanctions and consumer-protection rules in your jurisdictions.
            CRYPTUS ships no mandatory KYC module and collects no payer data; configure the policies your business requires.
          </Alert>
          <div className="grid-3">
            <Input label="Terms URL" value={str("terms_url")} onChange={set("terms_url")} disabled={!editable} />
            <Input label="Privacy policy URL" value={str("privacy_url")} onChange={set("privacy_url")} disabled={!editable} />
            <Input label="Refund policy URL" value={str("refund_policy_url")} onChange={set("refund_policy_url")} disabled={!editable} />
          </div>
          <Textarea label="Checkout notice" value={str("checkout_notice")} onChange={set("checkout_notice")} disabled={!editable}
            hint="Shown on every payment page (e.g. regulatory disclosures)." />
          <div className="grid-2">
            <Input label="Maximum invoice amount" type="number" value={str("max_invoice_amount")} disabled={!editable}
              onChange={(e) => setV({ ...v, max_invoice_amount: e.target.value ? Number(e.target.value) : null })}
              hint="In the default currency. Leave empty for no limit." />
            <Input label="Jurisdiction note (internal)" value={str("jurisdiction_note")} onChange={set("jurisdiction_note")} disabled={!editable} />
          </div>
          <Check label="Require payers to accept the terms before an address is shown" checked={!!v.require_terms_acceptance}
            disabled={!editable} onChange={(e) => setV({ ...v, require_terms_acceptance: e.target.checked })} />
        </>}
        {section === "security" && <div className="stack">
          <Input label="Session lifetime" type="number" suffix="hours" value={str("session_hours")} disabled={!editable}
            onChange={(e) => setV({ ...v, session_hours: Number(e.target.value) })} />
          <Check label="Require two-factor authentication for admins and owners" checked={!!v.require_mfa_for_admins}
            disabled={!editable} onChange={(e) => setV({ ...v, require_mfa_for_admins: e.target.checked })} />
        </div>}
        {section === "rates" && <div className="grid-2">
          <Input label="Cache duration" type="number" suffix="seconds" value={str("cache_seconds")} disabled={!editable}
            onChange={(e) => setV({ ...v, cache_seconds: Number(e.target.value) })} />
          <Input label="CoinGecko API key" type="password" value={str("api_key")} disabled={!editable} autoComplete="off"
            placeholder={initial.has_api_key ? "•••••• stored" : "optional (demo or pro)"} onChange={set("api_key")} />
          <Check label="Pro API plan" checked={!!v.pro} disabled={!editable} onChange={(e) => setV({ ...v, pro: e.target.checked })} />
          <p className="xs muted">Fiat-priced invoices fail closed when no rate is available — CRYPTUS never guesses a price.</p>
        </div>}
      </div>
    </Panel>
  );
}
