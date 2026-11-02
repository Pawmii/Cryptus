# Operations

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `CRYPTUS_ENV` | `production` | `development` auto-migrates and allows a generated secret |
| `CRYPTUS_DATABASE_URL` | SQLite in `./data` | Use `postgresql+psycopg://…` in production |
| `CRYPTUS_SECRET_KEY` / `_FILE` | — | Required in production. Signs sessions and encrypts stored secrets |
| `CRYPTUS_PUBLIC_URL` | `http://localhost:8000` | Used in checkout URLs; `https://` enables HSTS |
| `CRYPTUS_TRUSTED_PROXIES` | `["127.0.0.1","::1"]` | IPs/CIDRs whose `X-Forwarded-For` is honoured. The compose file pins the `edge` network to `172.30.250.0/24` and trusts only that |
| `CRYPTUS_COOKIE_SECURE` | `true` | Set `false` only for plain-HTTP local evaluation |
| `CRYPTUS_SIGNER_URL`, `CRYPTUS_SIGNER_TOKEN` / `_FILE` | — | Enable managed signing |
| `CRYPTUS_SETUP_TOKEN` | generated | Fix the installation token |
| `CRYPTUS_WEBHOOK_ALLOW_PRIVATE_TARGETS` | `false` | Allow webhooks to private networks (e.g. same host) |
| `CRYPTUS_WORKER_INTERVAL` | `10` | Seconds between worker cycles |

Networks, wallets, assets, policies and team are configured in the console and stored in the database.

## Network endpoints

| Network | Backend | Notes |
|---|---|---|
| Bitcoin | Bitcoin Core ≥ 24 (descriptor wallets) or Esplora | Core: `--profile bitcoind`. Wallet `cryptus-watch` is created automatically |
| EVM | Any JSON-RPC with `eth_getLogs` and batch requests | Archive access is **not** required. Increase `max_log_range` for providers that allow it |
| TON | Toncenter API (v2 + v3 indexer) | An API key is strongly recommended (rate limits). A self-hosted `ton-indexer` exposes the same API |

EVM adapter options (*Networks → Configure*, stored per network): `blocks_per_tick` (default 120),
`max_log_range` (1000), `reorg_rescan` (blocks re-read every pass, default `min(confirmations, 6)`),
`legacy_gas` (force `gasPrice` instead of EIP-1559).

Testnets: `btc-testnet` (testnet4/signet via Esplora), `eth-sepolia`, `bsc-testnet`, `polygon-amoy`,
`arbitrum-sepolia`, `base-sepolia`, `ton-testnet`. Test-mode API keys can only use these. Testnet coins are priced with their mainnet
counterpart's rate, so fiat-priced integration tests (`"currency": "USD"`) work in test mode.

## Managed signing setup

```bash
sudo chown -R 10001:10001 deploy/signer        # the container runs as uid 10001
docker compose -f deploy/docker-compose.yml run --rm signer cryptus-signer init --keystore /signer/keystore.json
cp deploy/signer/policy.example.json deploy/signer/policy.json   # edit allowlists and limits
echo "CRYPTUS_SIGNER_URL=http://signer:8700" >> deploy/.env
docker compose -f deploy/docker-compose.yml --profile managed-signing up -d
```

Then open *Wallets → Import signer keys*, attach the managed keychains, and set destinations that match
the policy allowlist. For maximum isolation, run the signer on a different host and connect the
`signing` network over WireGuard.

## Backups

| What | How | Why |
|---|---|---|
| PostgreSQL | `pg_dump` nightly + WAL archiving | Invoices, payments, derivation indexes |
| `deploy/secrets/cryptus_secret_key` | offline copy | Decrypts stored RPC credentials and webhook secrets |
| Signer keystore + passphrase | separate offline copies | Signing |
| Recovery phrases | paper/steel, offline | Last-resort fund recovery |

Derivation indexes are deterministic: even without the database, funds are recoverable by scanning
indexes `0…N` of each keychain.

## Upgrades

```bash
git pull && docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d     # the migrate service runs before web/worker
```

## Monitoring

* `GET /healthz` for liveness. Per-network health is shown in *Networks* (refreshed every 2 min by the worker).
* Subscribe a monitoring endpoint to `payment.orphaned`, `invoice.needs_review`, `invoice.reverted` and `sweep.failed`.
* `cryptus audit-verify` in a daily cron job: a non-zero exit means the audit chain was tampered with.

## Runbooks

**Suspected compromise.** Press *Consolidation → Emergency stop*, then `touch deploy/signer/HALT`, rotate
API keys and webhook secrets, revoke sessions (*Team → Disable*), and inspect the audit log and
`signer-audit.jsonl`.

**Network degraded.** Invoices on that network keep their addresses. Payments are detected as soon as
the backend recovers, because the scan cursor resumes where it stopped and the late-payment window
covers the gap.

**Payment arrived after expiry.** It is credited automatically within the late-payment window
(`paid_late`). After the window, the deposit is no longer watched, so find it via the explorer and
reconcile manually.

**Lost owner password.** `docker compose exec web cryptus reset-password owner@example.com`
