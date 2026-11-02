import { Lockup } from "../components/Logo";

export function AuthArt() {
  return (
    <aside className="auth__art" aria-hidden="true">
      <Lockup size={30} />
      <div className="auth__claim">
        <span className="label label--accent">Self-hosted payment infrastructure</span>
        <h2>Private payments.<br /><em>Open</em> infrastructure.</h2>
        <p>Accept digital assets directly into wallets you control. Unique deposit addresses, on-chain verification,
          and a signer that never trusts the web tier.</p>
      </div>
      <div className="auth__specs">
        {[["Custody", "Non-custodial"], ["Detection", "Per-invoice address"], ["Signing", "Isolated, policy-bound"]].map(([k, v]) => (
          <div key={k} className="stack--sm">
            <span className="label">{k}</span>
            <span className="small secondary">{v}</span>
          </div>
        ))}
      </div>
    </aside>
  );
}
