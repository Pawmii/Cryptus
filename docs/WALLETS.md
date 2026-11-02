# Wallet architecture & key ownership

CRYPTUS never needs a private key to accept payments. Deposit addresses are derived from **public** key
material that you register (watch-only) or that the isolated signer exports (managed). Before an address
can be issued, an operator must **verify** the keychain by pasting address #0 *as displayed by their own
wallet software*. This proves the gateway derives addresses that the merchant can actually spend.

## Operating modes

| | Watch-only | Managed signing |
|---|---|---|
| Who holds private keys | You (hardware wallet, offline machine) | `cryptus-signer`, on a separate host/container |
| Gateway holds | xpub / public key only | xpub / public key only |
| Consolidation | Gateway prepares an unsigned PSBT or tx. You sign it externally and paste it back | Signer validates the request against its **own** policy and signs |
| Failure mode if the web tier is compromised | Attacker sees balances and addresses; cannot move funds | Attacker can only *ask* the signer; destinations, fees, amounts and rates are capped by signer policy |

## Derivation per chain

| Chain | Standard | Deposits | Special keys |
|---|---|---|---|
| Bitcoin | BIP32 / **BIP84** (P2WPKH, `bc1q…`) | `m/84'/0'/0'/0/i` (testnet `m/84'/1'/0'/0/i`) | — |
| EVM (all) | BIP32 / BIP44 | `m/44'/60'/0'/0/i` | gas station `m/44'/60'/0'/1/0` |
| TON | ed25519 key + wallet **v4r2** | subwallet id `0x40000000 + i` | gas wallet = default subwallet `698983191` |

* A keychain's index counter is shared by every network it serves. One EVM xpub therefore never yields the
  same address for two invoices, even across different EVM chains.
* Indexes are allocated with an atomic `UPDATE … RETURNING` and enforced unique in the database.
* **Gap limit.** Unpaid invoices consume indexes. If you recover funds with a standard wallet, raise its gap
  limit (Sparrow: *Settings → Gap limit*), or use the address list exported by CRYPTUS.

### Bitcoin

Provide the account-level `zpub` (or `xpub`/`vpub`) **and** the master fingerprint. With Bitcoin Core,
CRYPTUS creates a blank, private-key-disabled descriptor wallet (`cryptus-watch`) and imports each deposit
address as `addr(…)` when it is allocated. With Esplora, addresses are polled.

Consolidation produces a BIP174 **PSBT** with `witness_utxo`, `non_witness_utxo` (when available) and full
BIP32 derivation paths. It is directly signable by Sparrow, Coldcard, Trezor, Ledger, or by
`cryptus-signer offline sign-psbt`. Before broadcasting an externally signed transaction, the gateway
checks that its outputs are byte-identical to the planned PSBT.

### EVM (Ethereum, BNB Smart Chain, Polygon, Arbitrum, Base)

* Every invoice gets a fresh externally-owned account derived from your xpub.
* Chain IDs are validated at health check and again by the signer.
* Token sweeps need native gas on the deposit address. CRYPTUS plans a **gas top-up** from the gas station
  address (fund it with a small amount), waits for it to confirm, then sweeps the token. In managed mode,
  the signer only permits top-ups *to its own deposit addresses* and below `max_gas_topup`.
* Every sweep is simulated with `eth_call` before signing.

### TON — native GRAM and Jettons

**Terminology.** Blockchain: TON (The Open Network). Native asset: **GRAM**, ticker `GRAM`, also used for
fees. Token standard: Jetton. USDT on TON is the USDT Jetton with master
`EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs`. Assets are identified by raw master address, so a
third-party token that also calls itself "GRAM" can never be confused with the native coin.

**Unique address per invoice.** A TON wallet's address is the hash of its `StateInit` (code + data), and the
v4r2 data cell contains a 32-bit `subwallet_id`. One ed25519 key therefore controls an unbounded family of
independent wallet contracts, one per subwallet id. CRYPTUS derives these addresses offline from your
public key. No memo or comment is needed, and none is used for identification.

**Deployment and activation.** Deposit wallets are **not deployed** in advance. Funds sent to an
uninitialised address with `bounce=false` are credited to the account. The checkout therefore shows only
the **non-bounceable** form (`UQ…`, testnet `0Q…`). The first outgoing message (the sweep) carries the
`StateInit` and deploys the wallet in the same transaction. Uninitialised accounts accrue small storage
fees. Sweep periodically.

**Jettons.** For each deposit owner, CRYPTUS asks the verified jetton master for the owner's jetton
wallet address (`get_wallet_address`). That address is derived from the master's jetton-wallet code, so
a successful `internal_transfer` executed there can only originate from a genuine wallet of the same
master. Detection additionally requires that:

* the incoming body parses as `internal_transfer` (op `0x178d4519`) and supplies the amount and sender,
* the compute and action phases succeeded and the transaction was not aborted,
* once deployed, `get_wallet_data` reports **owner = deposit wallet** and **master = configured master**.

A mismatch marks the payment `rejected`. Unverified jetton wallets are never swept.

**Jetton consolidation** requires GRAM on the deposit wallet (≥ 0.1 GRAM) for deployment and the
transfer message. CRYPTUS tops up from the gas wallet (your default v4r2 wallet for the same key, the one
Tonkeeper shows), then sends `transfer` (op `0x0f8a7ea5`) with `destination` and `response_destination`
set to your primary wallet. Excess GRAM is returned there.

**Recovering TON funds without CRYPTUS.** You need the key and the subwallet id (`0x40000000 + index`,
shown on each invoice's *Quotes & derivation* panel). `cryptus-signer offline sign-ton` builds the
external message. Any v4r2-capable library can do the same.

## Consolidation safety rules

1. A destination must be set **and confirmed** by an operator (recorded in the audit log). In managed mode
   it must also be on the **signer's allowlist**, which lives in the signer's own policy file.
2. Plans are built from **live chain data** (UTXOs, balances), never from database records. Balances are
   re-read again immediately before signing. A changed balance invalidates the sweep.
3. Each sweep has a deterministic idempotency key (unique constraint).
4. Fees are estimated and capped per network. Sweeps above the cap are skipped.
5. Approval is required by default (`treasury.require_approval`).
6. **Emergency stop** in the console halts planning, approval, signing and broadcast. A `HALT` file next to
   the signer policy stops the signer independently.
7. Failures retry at most three times with back-off, then stop as `failed`.

## Signer policy

`deploy/signer/policy.example.json` documents every field:

* `allowlist` – destinations per network (normalised comparison),
* `max_fee`, `max_feerate_sat_vb` – fee caps,
* `max_gas_topup`, `max_jetton_attach` – caps for internal funding,
* `tokens` – permitted token contracts,
* `daily_limits` – rolling 24 h totals per canonical asset id (atomic units).

Every decision, signed or refused, is appended to `signer-audit.jsonl`, a hash-chained, fsync'd log
that the gateway cannot write to. Request ids are single-use, and requests older than 60 s are rejected.

## Key storage

* The keystore is encrypted with AES-256-GCM. The key-encryption key is either scrypt-derived from a
  passphrase (read from a Docker secret file, never from argv or the general environment), or an AWS KMS
  data key (`--kms-key-id`, envelope encryption). HSM integrations can implement the same two-function
  interface in `cryptus_signer/keystore.py`.
* `cryptus-signer init` prints both recovery phrases exactly once. Store them offline. The BIP39 phrase
  covers Bitcoin and EVM. TON uses its native 24-word scheme.
* Private keys never appear in frontend code, logs, the database or the gateway environment.
