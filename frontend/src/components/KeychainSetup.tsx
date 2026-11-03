import { useState } from "react";
import { api } from "../lib/api";
import type { Keychain, NetworkRow } from "../lib/types";
import { Icon } from "./Icon";
import { Alert, Button, CopyField, Input, Textarea, useToast } from "./ui";

export function familyScope(net: NetworkRow) {
  return net.family === "evm" ? "evm" : net.key;
}

const MATERIAL_HINT: Record<string, string> = {
  bitcoin: "Account-level extended PUBLIC key (zpub / xpub for mainnet, vpub / tpub for testnet) at m/84'/coin'/0'. Export it from Sparrow, Electrum, or your hardware wallet.",
  evm: "Account-level xpub at m/44'/60'/0'. One EVM keychain serves every EVM network; addresses never collide across chains.",
  ton: "32-byte ed25519 public key (64 hex chars). Each invoice gets its own wallet v4r2 contract (unique subwallet id) controlled by this key.",
};

type Mode = "watch_only" | "direct";

/** Register watch-only public material (or, on TON, the merchant's own wallet), then prove it is the right one. */
export function KeychainSetup({ net, onDone }: { net: NetworkRow; onDone: (kc: Keychain) => void }) {
  const toast = useToast();
  const [mode, setMode] = useState<Mode>(net.family === "ton" ? "direct" : "watch_only");
  const [material, setMaterial] = useState("");
  const [fingerprint, setFingerprint] = useState("");
  const [path, setPath] = useState(net.environment === "mainnet" ? "m/84'/0'/0'" : "m/84'/1'/0'");
  const [label, setLabel] = useState("");
  const [kc, setKc] = useState<Keychain | null>(null);
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const direct = mode === "direct";

  async function create() {
    setPending(true);
    setError(null);
    try {
      setKc(await api.post<Keychain>("/api/admin/keychains", {
        family: net.family, scope: familyScope(net), mode, public_material: material,
        label: label || (direct ? `${net.name} wallet` : `${net.name} treasury`),
        master_fingerprint: net.family === "bitcoin" ? fingerprint : null, account_path: net.family === "bitcoin" ? path : null,
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function verify() {
    if (!kc) return;
    setPending(true);
    setError(null);
    try {
      const v = await api.post<Keychain>(`/api/admin/keychains/${kc.id}/verify`, { address: confirm });
      await api.put(`/api/admin/wallets/${net.key}`, { keychain_id: kc.id });
      toast("success", "Wallet verified and attached");
      onDone(v);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (kc) {
    return (
      <div className="stack">
        <Alert tone="warning" title={direct ? "Confirm it is your wallet" : "Verify before accepting payments"}>
          {direct
            ? <>Open your wallet app, tap <strong>Receive</strong> and compare the address character by character. Then paste it
              again below. Every payment will be sent to this address.</>
            : <>Open your own wallet software with the same key and compare the first receive address. Paste it below.
              CRYPTUS will not issue addresses from an unverified keychain.</>}
        </Alert>
        <div className="stack--sm">
          <span className="label">{direct ? "Payments will go to" : "Derived by CRYPTUS"}</span>
          {kc.preview?.map((p) => (
            <div key={p.index} className="row row--nowrap">
              {!direct && <span className="tag">#{p.index}</span>}
              <div className="grow"><CopyField value={p.address} label="address" /></div>
            </div>
          ))}
        </div>
        <Input label={direct ? "Your wallet address, pasted again" : "Address #0 as shown by your wallet"} mono value={confirm}
          onChange={(e) => setConfirm(e.target.value)} error={error} />
        <div className="row row--end">
          <Button variant="ghost" onClick={() => setKc(null)}>Back</Button>
          <Button variant="primary" onClick={verify} loading={pending} disabled={!confirm}>Verify & attach</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      {error && <Alert tone="danger">{error}</Alert>}
      {net.family === "ton" && (
        <div className="stack--sm" role="radiogroup" aria-label="How payments reach you">
          <span className="label">How payments reach you</span>
          <ModeOption active={direct} onClick={() => setMode("direct")} icon="wallet" title="Straight to my wallet"
            text="Customers pay your own wallet. Each invoice gets a unique comment (memo) that CRYPTUS matches. No keys on the server, nothing to sweep." />
          <ModeOption active={!direct} onClick={() => setMode("watch_only")} icon="key" title="Separate address per invoice"
            text="No memo needed. Funds are collected into your wallet afterwards. For automatic collection run deploy/signer-setup.sh and use Wallets → Import signer keys instead of this form." />
        </div>
      )}
      {direct ? (
        <>
          <Input label="Your TON wallet address" mono value={material} onChange={(e) => setMaterial(e.target.value.trim())}
            placeholder={net.environment === "mainnet" ? "UQ…" : "0Q…"}
            hint="Copy it from the Receive screen of Tonkeeper, MyTonWallet, Tonhub or another self-custodial wallet." />
          <Alert tone="warning" title="Use a wallet you hold the recovery phrase for">
            Never enter an exchange deposit address: exchanges match deposits by their own memo, so payments would be lost.
          </Alert>
        </>
      ) : (
        <Textarea label={net.family === "ton" ? "Public key" : "Extended public key"} mono rows={3} value={material}
          onChange={(e) => setMaterial(e.target.value.trim())} hint={MATERIAL_HINT[net.family]}
          placeholder={net.family === "ton" ? "e.g. 3f7a…(64 hex)" : net.family === "bitcoin" ? "zpub6r…" : "xpub6C…"} />
      )}
      {net.family === "bitcoin" && (
        <div className="grid-2">
          <Input label="Master fingerprint" mono maxLength={8} value={fingerprint} placeholder="73c5da0a"
            onChange={(e) => setFingerprint(e.target.value.trim().toLowerCase())}
            hint="Required so hardware wallets can sign consolidation PSBTs." />
          <Input label="Account path" mono value={path} onChange={(e) => setPath(e.target.value)} />
        </div>
      )}
      <Input label="Label" value={label} onChange={(e) => setLabel(e.target.value)}
        placeholder={direct ? `${net.name} wallet` : `${net.name} treasury`} />
      <Alert tone="info">Only public information is accepted. Never paste a seed phrase or private key into CRYPTUS.</Alert>
      <div className="row row--end">
        <Button variant="primary" onClick={create} loading={pending} disabled={!material}>{direct ? "Continue" : "Derive addresses"}</Button>
      </div>
    </div>
  );
}

function ModeOption({ active, onClick, icon, title, text }: {
  active: boolean; onClick: () => void; icon: "wallet" | "key"; title: string; text: string;
}) {
  return (
    <button type="button" role="radio" aria-checked={active} aria-pressed={active} className="asset-option" onClick={onClick}>
      <span className="asset-glyph"><Icon name={icon} size={16} /></span>
      <span className="grow vstack">
        <strong style={{ fontWeight: 600 }}>{title}</strong>
        <span className="xs muted">{text}</span>
      </span>
    </button>
  );
}
