<p align="center">
  <img src="frontend/public/favicon.svg" width="64" alt="CRYPTUS seal" />
</p>

<h1 align="center">CRYPTUS</h1>
<p align="center"><b>Private Payments. Open Infrastructure.</b></p>

CRYPTUS is a self-hosted, non-custodial cryptocurrency payment gateway. Merchants accept digital assets
directly into wallets they control: every invoice gets its own deposit address (or, on TON, a unique comment
for payments straight to the merchant's own wallet), payments are detected and verified on-chain, and
optional consolidation moves funds to a primary wallet under strict policy.

| | |
|---|---|
| **Networks** | Bitcoin · TON (native **GRAM** + Jettons, incl. USDT Jetton) · Ethereum · BNB Smart Chain · Polygon · Arbitrum · Base — plus their testnets |
| **Planned** | Litecoin · Dogecoin · TRON · Solana (shown as *planned*, never as operational) |
| **Custody** | Watch-only by default (public keys only). Optional isolated signer for automated sweeps |
| **Identification** | Unique deposit address per invoice. On TON, optionally **direct to your wallet** with a unique comment per invoice — no keys on the server, nothing to sweep |
| **Privacy** | No payer accounts, no payer personal data, no third-party scripts or fonts on the checkout |
| **Stack** | Python 3.11+ · FastAPI · SQLAlchemy/Alembic · PostgreSQL · React + TypeScript · Docker |

> ⚠️ **Self-hosting does not remove legal obligations.** Depending on your jurisdiction you may need
> licences, tax reporting, sanctions screening or consumer-protection measures. CRYPTUS provides
> configurable policies (terms acceptance, limits, notices) but no legal compliance by itself.
> See [docs/COMPLIANCE.md](docs/COMPLIANCE.md).

## Screenshots

> The demo instance uses real connections to Bitcoin, Ethereum, Base, TON and Sepolia and real CoinGecko rates.
> Its payments and invoice dates are simulated so that every interface state shows.

<p align="center">
  <img src="docs/screenshots/05-overview.png" alt="Operator console overview: settled volume, conversion, network health" width="100%" />
</p>

| Checkout: choose how to pay | Checkout: awaiting confirmations |
|---|---|
| ![Asset selection](docs/screenshots/22-checkout-choose-asset.png) | ![Payment detected, confirmations filling](docs/screenshots/23-checkout-awaiting.png) |
| **TON: straight to your wallet** | **Checkout with the payment comment** |
| ![Attach a TON wallet in direct mode](docs/screenshots/28-ton-direct-wallet.png) | ![Checkout showing address and required comment](docs/screenshots/29-checkout-ton-memo.png) |
| **Invoice detail** | **Consolidation & emergency stop** |
| ![Invoice detail](docs/screenshots/07-invoice-detail.png) | ![Consolidation](docs/screenshots/09-consolidation.png) |
| **Networks** | **Installation wizard** |
| ![Networks](docs/screenshots/11-networks.png) | ![Setup wizard](docs/screenshots/04-setup-networks.png) |
| **Light theme ("Ledger")** | **Sign in** |
| ![Light theme](docs/screenshots/21-light-theme-overview.png) | ![Login](docs/screenshots/25-login.png) |

<p align="center">
  <img src="docs/screenshots/26-mobile-checkout.png" alt="Mobile checkout" width="260" />
  &nbsp;&nbsp;
  <img src="docs/screenshots/27-mobile-login.png" alt="Mobile login" width="260" />
</p>

<details>
<summary>All screens</summary>

| | |
|---|---|
| ![](docs/screenshots/01-setup-system.png) System check | ![](docs/screenshots/02-setup-owner.png) Owner account |
| ![](docs/screenshots/03-setup-business.png) Business profile | ![](docs/screenshots/06-invoices.png) Invoices |
| ![](docs/screenshots/08-payments.png) Payments | ![](docs/screenshots/19-new-invoice-dialog.png) New invoice |
| ![](docs/screenshots/18-sweep-dialog.png) Sweep approval | ![](docs/screenshots/10-wallets.png) Wallets |
| ![](docs/screenshots/12-assets.png) Assets | ![](docs/screenshots/13-api-keys.png) API keys |
| ![](docs/screenshots/14-webhooks.png) Webhooks | ![](docs/screenshots/15-team.png) Team |
| ![](docs/screenshots/16-audit.png) Audit log | ![](docs/screenshots/17-settings.png) Settings |
| ![](docs/screenshots/24-checkout-paid.png) Checkout: paid | ![](docs/screenshots/20-light-theme-account.png) Account & theme |

</details>

## Quick start (evaluation, testnets)

```bash
# backend
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e "backend[dev]"
cd frontend && npm ci && npm run build && cd ..
CRYPTUS_ENV=development CRYPTUS_COOKIE_SECURE=false cryptus serve --workers 1   # http://localhost:8000
CRYPTUS_ENV=development cryptus worker                                          # second terminal
```

The server prints a **setup token**. Open `http://localhost:8000`, enter it, and the installation wizard
walks you through the owner account, business profile, networks and wallets. Use the **Testnet** tab
and a test-mode API key to rehearse the whole flow with valueless coins.

## Production

On a fresh Ubuntu/Debian server whose domain already points at it:

```bash
git clone https://github.com/Pawmii/Cryptus.git /opt/cryptus
cd /opt/cryptus && sudo sh deploy/install.sh pay.example.com   # Docker, secrets, build, start, setup token
```

**[docs/PRODUCTION.md](docs/PRODUCTION.md)** walks from there to the first real payment in your own TON
wallet: wizard, wallet, 2FA, backups and shop integration. Caddy terminates TLS automatically.
Optional profiles: `--profile managed-signing` (isolated signer), `--profile bitcoind` (your own Bitcoin
Core node). Manual steps and runbooks are in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Merchant API in one request

```bash
curl https://pay.example.com/api/v1/invoices \
  -H "Authorization: Bearer ck_live_…" -H "Idempotency-Key: order-1042" \
  -H "Content-Type: application/json" \
  -d '{"amount":"25.00","currency":"USD","order_id":"1042","redirect_url":"https://shop.example/thanks"}'
```

Redirect the customer to `checkout_url`, then wait for the signed `invoice.paid` webhook.
Full reference: [docs/API.md](docs/API.md) and the live OpenAPI docs at `/api/docs`.

## Documentation

| Document | Contents |
|---|---|
| [PRODUCTION](docs/PRODUCTION.md) | Going live step by step: server, install, TON wallet, 2FA, backups, shop integration |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Components, data model, invoice state machine, monitoring and reorg handling |
| [WALLETS](docs/WALLETS.md) | Key ownership, derivation per chain, TON wallet design, consolidation, signer policy |
| [SECURITY](docs/SECURITY.md) | Threat model, controls, hardening checklist |
| [API](docs/API.md) | Merchant API, webhooks, signature verification |
| [OPERATIONS](docs/OPERATIONS.md) | Deployment, backups, upgrades, monitoring, incident runbooks |
| [COMPLIANCE](docs/COMPLIANCE.md) | Legal responsibilities, data minimisation, configurable policies |
| [DESIGN](docs/DESIGN.md) | The CRYPTUS visual identity and design system |

## Tests

```bash
cd backend && pytest -q        # derivation vectors, signer policy, end-to-end payment lifecycle
cd frontend && npm run build   # type-check + production bundle
```

## License

AGPL-3.0-or-later. Running a modified CRYPTUS as a network service obliges you to publish your changes.
