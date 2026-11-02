import { useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { KeychainSetup } from "../components/KeychainSetup";
import { Lockup } from "../components/Logo";
import { NetworkForm } from "../components/NetworkForm";
import { Alert, Button, Input, LoadingBlock, Panel, Select, Status } from "../components/ui";
import { api, setCsrf } from "../lib/api";
import { useApi } from "../lib/hooks";
import { useSession, type User } from "../lib/session";
import type { NetworkRow } from "../lib/types";

interface SetupStatus {
  version: string; has_owner: boolean;
  checks: Record<string, { ok?: boolean; note?: string | null; value?: string; dialect?: string; configured?: boolean; error?: string }>;
}

const STEPS = ["System check", "Owner account", "Business profile", "Networks", "Wallets", "Ready"];
const CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "SGD", "AED", "PLN", "TRY", "BRL", "INR", "UAH", "KZT"];

export function Setup({ hasOwner }: { hasOwner: boolean }) {
  const { user, setSession } = useSession();
  const nav = useNavigate();
  const [step, setStep] = useState(hasOwner ? 3 : 0);
  const status = useApi<SetupStatus>("/api/setup/status");
  const [token, setToken] = useState("");
  const [owner, setOwner] = useState({ email: "", name: "", password: "", confirm: "" });
  const [biz, setBiz] = useState({ name: "", website: "", support_email: "", default_currency: "USD", default_expiry_minutes: 30 });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (hasOwner && !user) return <Navigate to="/login" replace />;
  if (!status.data) return <LoadingBlock />;

  async function checkToken() {
    setPending(true);
    setError(null);
    try {
      await api.post("/api/setup/verify-token", { token });
      setStep(1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function initialize() {
    setPending(true);
    setError(null);
    try {
      const r = await api.post<{ user: User; csrf_token: string }>("/api/setup/initialize", {
        token, owner: { email: owner.email, name: owner.name, password: owner.password }, business: biz,
      });
      setCsrf(r.csrf_token);
      setSession(r.user, r.csrf_token);
      setStep(3);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  const pwIssues = owner.password && owner.password.length < 12 ? "At least 12 characters" :
    owner.confirm && owner.confirm !== owner.password ? "Passwords do not match" : null;

  return (
    <div className="wizard">
      <aside className="wizard__rail">
        <Lockup size={26} />
        <div className="steps" aria-label="Installation progress">
          {STEPS.map((s, i) => (
            <div key={s} className="step" data-state={i < step ? "done" : i === step ? "current" : "todo"}>
              <span className="step__n"><span>{i < step ? "✓" : i + 1}</span></span>
              <span>{s}</span>
            </div>
          ))}
        </div>
        <span className="xs faint mono">CRYPTUS v{status.data.version}</span>
      </aside>

      <main className="wizard__main">
        <div className="wizard__card" key={step}>
          <div className="stack--sm">
            <span className="label label--accent">Step {step + 1} of {STEPS.length}</span>
            <h1>{["Prepare the vault", "Create the owner account", "Describe your business",
              "Connect blockchain networks", "Attach merchant wallets", "CRYPTUS is ready"][step]}</h1>
          </div>
          <div className="seam" />
          {error && <Alert tone="danger">{error}</Alert>}

          {step === 0 && (
            <>
              <Panel title="Environment" flush>
                <table className="table">
                  <tbody>
                    {Object.entries(status.data.checks).map(([k, c]) => (
                      <tr key={k}>
                        <td style={{ width: 170 }} className="label">{k.replace("_", " ")}</td>
                        <td>
                          {k === "signer" ? (c.configured ? <Status value={c.ok ? "operational" : "degraded"} />
                            : <span className="small muted">Not configured — watch-only mode available</span>)
                            : <Status value={c.ok ? "operational" : "degraded"} />}
                        </td>
                        <td className="small muted">{c.note ?? c.value ?? c.dialect ?? c.error ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
              <Input label="Setup token" mono value={token} onChange={(e) => setToken(e.target.value)}
                hint={<>Printed in the server log at startup and stored in <code>data/setup-token</code>. Run <code>cryptus setup-token</code> to display it.</>} />
              <div className="row row--end">
                <Button variant="primary" iconRight="arrowRight" onClick={checkToken} loading={pending} disabled={!token}>Continue</Button>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <p className="muted">The owner holds every permission, including lifting the treasury emergency stop. Add
                operators with narrower roles later.</p>
              <div className="grid-2">
                <Input label="Full name" value={owner.name} onChange={(e) => setOwner({ ...owner, name: e.target.value })} autoFocus />
                <Input label="Email" type="email" autoComplete="username" value={owner.email}
                  onChange={(e) => setOwner({ ...owner, email: e.target.value })} />
                <Input label="Password" type="password" autoComplete="new-password" value={owner.password}
                  onChange={(e) => setOwner({ ...owner, password: e.target.value })} hint="12+ characters. A passphrase works best." />
                <Input label="Confirm password" type="password" autoComplete="new-password" value={owner.confirm}
                  onChange={(e) => setOwner({ ...owner, confirm: e.target.value })} error={pwIssues} />
              </div>
              <div className="row row--between">
                <Button variant="ghost" icon="arrowLeft" onClick={() => setStep(0)}>Back</Button>
                <Button variant="primary" iconRight="arrowRight" onClick={() => setStep(2)}
                  disabled={!owner.name || !owner.email || owner.password.length < 12 || owner.password !== owner.confirm}>Continue</Button>
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <p className="muted">Shown to payers on the checkout page. CRYPTUS collects no customer personal data.</p>
              <div className="grid-2">
                <Input label="Business name" value={biz.name} onChange={(e) => setBiz({ ...biz, name: e.target.value })} autoFocus />
                <Input label="Website" value={biz.website} placeholder="https://" onChange={(e) => setBiz({ ...biz, website: e.target.value })} />
                <Input label="Support email" value={biz.support_email} onChange={(e) => setBiz({ ...biz, support_email: e.target.value })} />
                <Select label="Default pricing currency" value={biz.default_currency}
                  onChange={(e) => setBiz({ ...biz, default_currency: e.target.value })}>
                  {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
                </Select>
                <Input label="Invoice lifetime" type="number" min={5} suffix="minutes" value={biz.default_expiry_minutes}
                  onChange={(e) => setBiz({ ...biz, default_expiry_minutes: Number(e.target.value) })}
                  hint="The quoted rate is locked for this window." />
              </div>
              <div className="row row--between">
                <Button variant="ghost" icon="arrowLeft" onClick={() => setStep(1)}>Back</Button>
                <Button variant="primary" iconRight="arrowRight" onClick={initialize} loading={pending} disabled={!biz.name}>
                  Create account
                </Button>
              </div>
            </>
          )}

          {step === 3 && <NetworksStep onNext={() => setStep(4)} />}
          {step === 4 && <WalletsStep onBack={() => setStep(3)} onNext={() => setStep(5)} />}

          {step === 5 && (
            <>
              <Alert tone="success" title="Installation complete">
                Next: create an API key for your store, register a webhook endpoint, and configure consolidation if you
                want funds swept to a primary wallet.
              </Alert>
              <div className="grid-3">
                {([["key", "Create API key", "/developers/api-keys"], ["webhook", "Add webhook", "/developers/webhooks"],
                  ["sweep", "Consolidation", "/wallets"]] as const).map(([ic, t, to]) => (
                  <button key={to} className="asset-option" onClick={() => nav(to)}>
                    <Icon name={ic} /> <span className="grow">{t}</span> <Icon name="chevron" size={14} />
                  </button>
                ))}
              </div>
              <div className="row row--end">
                <Button variant="primary" iconRight="arrowRight" onClick={() => nav("/")}>Open the console</Button>
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

function NetworksStep({ onNext }: { onNext: () => void }) {
  const nets = useApi<NetworkRow[]>("/api/admin/networks");
  const [env, setEnv] = useState<"mainnet" | "testnet">("mainnet");
  const [open, setOpen] = useState<string | null>(null);
  const list = useMemo(() => (nets.data ?? []).filter((n) => n.implementation === "implemented" && n.environment === env), [nets.data, env]);
  if (!nets.data) return <LoadingBlock />;
  const configured = nets.data.filter((n) => n.enabled).length;
  return (
    <>
      <p className="muted">Enable the networks you want to accept. Start on testnets to rehearse the full flow with
        valueless coins — test-mode API keys only ever quote testnet assets.</p>
      <div className="row">
        <div className="segmented" role="group" aria-label="Environment">
          {(["mainnet", "testnet"] as const).map((e) => (
            <button key={e} aria-pressed={env === e} onClick={() => setEnv(e)}>{e === "mainnet" ? "Mainnet" : "Testnet"}</button>
          ))}
        </div>
      </div>
      <div className="stack--sm">
        {list.map((n) => (
          <Panel key={n.key} title={<span className="row">{n.name} <span className="tag">{n.native_symbol}</span></span>}
            actions={<><Status value={n.status} /><Button size="sm" variant="ghost" icon={open === n.key ? "chevronDown" : "chevron"}
              onClick={() => setOpen(open === n.key ? null : n.key)} aria-label="Configure" /></>}>
            {open === n.key ? <NetworkForm net={n} onSaved={() => nets.reload()} /> :
              <span className="small muted">{n.endpoint_url ?? "No endpoint configured"}</span>}
          </Panel>
        ))}
      </div>
      <div className="row row--end">
        <Button variant={configured ? "primary" : "secondary"} iconRight="arrowRight" onClick={onNext}>
          {configured ? "Continue" : "Skip for now"}
        </Button>
      </div>
    </>
  );
}

function WalletsStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const nets = useApi<NetworkRow[]>("/api/admin/networks");
  const [sel, setSel] = useState<string | null>(null);
  const [signerMsg, setSignerMsg] = useState<string | null>(null);
  const enabled = (nets.data ?? []).filter((n) => n.enabled);
  useEffect(() => { if (!sel && enabled[0]) setSel(enabled[0].key); }, [enabled, sel]);
  if (!nets.data) return <LoadingBlock />;
  const net = enabled.find((n) => n.key === sel);

  async function importSigner() {
    try {
      const r = await api.post<{ created: unknown[] }>("/api/admin/keychains/import-signer");
      setSignerMsg(`${r.created.length} managed keychain(s) imported from the signer. Attach them on the Wallets page.`);
    } catch (e) {
      setSignerMsg((e as Error).message);
    }
  }

  return (
    <>
      <p className="muted">Provide public key material from wallets you control. CRYPTUS derives a fresh deposit
        address for every invoice and never needs your private keys in watch-only mode.</p>
      {enabled.length === 0 ? <Alert tone="warning">Enable at least one network first.</Alert> : (
        <>
          <div className="row">
            {enabled.map((n) => (
              <button key={n.key} className="btn btn--sm btn--secondary" aria-pressed={sel === n.key}
                style={sel === n.key ? { boxShadow: "inset 0 0 0 1px var(--accent)" } : undefined} onClick={() => setSel(n.key)}>
                {n.wallet && <Icon name="check" size={13} />}{n.name}
              </button>
            ))}
          </div>
          {net && (net.wallet ? (
            <Alert tone="success" title={`${net.name} wallet attached`}>{net.wallet.keychain?.label} · {net.wallet.keychain?.mode.replace("_", "-")}</Alert>
          ) : (
            <Panel title={`${net.name} — watch-only keychain`}><KeychainSetup net={net} onDone={() => nets.reload()} /></Panel>
          ))}
        </>
      )}
      <Panel title="Managed signing (optional)" eyebrow="Isolated signer">
        <div className="stack">
          <p className="small muted">If you run <code>cryptus-signer</code> on a separate host, import its public keys to enable
            automatic, policy-bound consolidation.</p>
          {signerMsg && <Alert>{signerMsg}</Alert>}
          <div><Button size="sm" icon="signer" onClick={importSigner}>Import from signer</Button></div>
        </div>
      </Panel>
      <div className="row row--between">
        <Button variant="ghost" icon="arrowLeft" onClick={onBack}>Back</Button>
        <Button variant="primary" iconRight="arrowRight" onClick={onNext}>Continue</Button>
      </div>
    </>
  );
}
