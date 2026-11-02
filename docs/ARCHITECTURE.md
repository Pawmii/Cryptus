# Architecture

```
            ┌──────────── payer browser ────────────┐        ┌──── merchant store ────┐
            │ /pay/{id}  (no cookies, no 3rd party) │        │ REST /api/v1  webhooks │
            └───────────────────┬───────────────────┘        └───────────┬────────────┘
                                │ HTTPS                                   │
                         ┌──────▼──────┐                                  │
                         │    Caddy    │  TLS, compression                 │
                         └──────┬──────┘                                  │
 edge network ─────────────────┼──────────────────────────────────────────┘
                         ┌──────▼──────┐      backend (internal)     ┌────────────┐
                         │  web (API + │◄───────────────────────────►│ PostgreSQL │
                         │  console)   │                              └─────▲──────┘
                         └──────┬──────┘                                    │
                                │ signing (internal)                  ┌─────┴──────┐
                         ┌──────▼──────┐◄──────────────────────────── │   worker   │
                         │   signer    │   HMAC-authenticated          │ monitor /  │
                         │ (optional)  │   structured requests         │ sweeps /   │
                         └─────────────┘                               │ webhooks   │
                                                                       └─────┬──────┘
                                                      chains (egress)        │
                                         Bitcoin Core / Esplora · EVM RPC · Toncenter
```

## Components

| Component | Code | Responsibility |
|---|---|---|
| Core | `backend/cryptus/core` | Pure primitives shared with the signer: exact money, network/asset registry, key derivation, TON wallet contract, ERC-20 ABI |
| Chain adapters | `backend/cryptus/chains` | One independent adapter per family behind a common interface: `health`, `prepare_deposit`, `scan`, `refresh`, `balance`, `broadcast` |
| Services | `backend/cryptus/services` | Invoices, monitor, consolidation, webhooks, rates, audit, infra management |
| API | `backend/cryptus/api` | Console API (session + CSRF), merchant API (bearer keys), public checkout API, setup wizard |
| Worker | `backend/cryptus/worker.py` | Single active instance (DB lease); monitors chains, expires invoices, delivers webhooks, runs sweeps |
| Signer | `backend/cryptus_signer` | Separate process: encrypted keystore, independent policy, hash-chained audit, offline CLI |
| Frontend | `frontend/` | React console, setup wizard and checkout built on the CRYPTUS design system |

## Asset identity

Assets are keyed by **canonical ids**, never tickers:

```
btc:native
ton:native                                   (GRAM — the native coin of TON)
ton:jetton:0:b113a994…3621dfe                (USDT Jetton, raw master address)
eth:erc20:0xdAC17F958D2ee523a2206206994597C13D831ec7
```

Built-in tokens ship with verified contract addresses and decimals. Operator-added tokens are inspected
on-chain (contract code present, decimals match) before they can be quoted. Different networks never share
an asset id even when the ticker matches.

## Invoice state machine

```
pending ──payment seen──► processing ──confirmed ≥ due──► paid
   │                          │
   └─expiry, nothing seen─► expired      expiry, partial confirmed ─► underpaid
pending/processing ──operator──► cancelled
```

* Payments detected after expiry (within the *late-payment window*, default 24 h) still settle the invoice
  and set `paid_late`. Excess sets `overpaid`. Payments to more than one quote, or to a cancelled invoice,
  set `needs_review`. Funds are never silently ignored.
* If a payment that settled an invoice is reorganised away, the invoice reverts and emits
  `invoice.reverted`.
* Fiat-priced invoices lock a rate when the payer picks an asset. The rate and its source are stored on
  the deposit record. Quotes round **up**.

## Monitoring pipeline (per network, every worker tick)

1. **scan** – the adapter finds transfers to watched addresses (deposits whose `monitor_until` lies in the future).
2. **upsert** – payments are idempotent on `(network, txid, output index, asset)`.
3. **refresh** – every pending payment, and every confirmed one below `required × 3` confirmations, is
   re-verified: confirmations, canonical block hash and amount. Missing transactions become `orphaned`.
   Failed or mismatching ones become `rejected`.
4. **reconcile** (EVM native) – the on-chain balance at the confirmation depth is compared with attributed
   payments, so value delivered by contract wallets (internal transactions) is still credited.
5. **recompute** – invoice states are derived from payments, and events and webhooks are emitted.

| Family | Detection | Finality / reorgs |
|---|---|---|
| Bitcoin (Core) | watch-only descriptor wallet, `listsinceblock` | `gettransaction` confirmations; negative = conflicted |
| Bitcoin (Esplora) | `/address/{a}/txs` per address | `/tx/{id}/status`; disappearance ⇒ orphaned |
| EVM | `eth_getLogs` (ERC-20) + batched full blocks (native) with receipt status | receipt re-fetch, block-hash comparison |
| TON | Toncenter v3 transactions of deposit wallet / jetton wallet | masterchain seqno distance |

## Data model (abridged)

`keychains` (public material, monotonic `next_index`) → `network_wallets` (network ↔ keychain, destination,
sweep policy) → `deposit_addresses` (unique per `(keychain, index)` and per `(network, address)`) →
`payments`. `invoices` reference their active deposit. `sweeps` carry an idempotency key, artifacts
(PSBT / unsigned tx) and status. `audit_log` is hash-chained. `events` + `webhook_deliveries` implement
the outbox.

Amounts are stored as **integer strings in atomic units** (exact for uint256 on every database). Floats are
never used for money.
