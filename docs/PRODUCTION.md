# Going live

This guide takes a fresh server to accepting real payments into **your own wallet**. On TON you choose how
payments reach it:

| | Per-order addresses (recommended for shops) | Straight to my wallet |
|---|---|---|
| What the buyer sees | A fresh address for every order, **no comment needed** | Your wallet address + a required comment |
| How money reaches your wallet | Collected automatically by the isolated signer (a few minutes after payment) | Directly |
| Keys on the server | The signer's own seed (not your wallet's), bound by a policy that can only pay your wallet | None |
| Cost per order | Network fees of the collection (≈ 0.001 GRAM; USDT ≈ 0.01–0.02 GRAM incl. top-up) | None |
| Set up | Sections 3 and 4 below | Section 3, option B |

Bitcoin and EVM networks follow the same steps with their own wallet types (see [WALLETS](WALLETS.md)).

## 1. What you need

| | |
|---|---|
| Server | Ubuntu 22.04/24.04 or Debian 12, 2 vCPU, 2 GB RAM, 20 GB disk, public IPv4 |
| Domain | e.g. `pay.example.com`, with an **A record** pointing at the server. TCP **80** and **443** open |
| Wallet | A self-custodial TON wallet whose recovery phrase you hold: Tonkeeper, MyTonWallet, Tonhub… **Not an exchange address** |
| Toncenter API key | Telegram → [@tonapibot](https://t.me/tonapibot) → create a **mainnet** key. Without a key Toncenter allows ~1 request/s and detection slows down |

## 2. Install

```bash
ssh root@your-server
apt-get update && apt-get install -y git
git clone https://github.com/Pawmii/Cryptus.git /opt/cryptus
cd /opt/cryptus && sh deploy/install.sh pay.example.com
```

The script installs Docker if needed and writes `deploy/.env` and `deploy/secrets/` (random passwords and
keys). It then builds the image, starts PostgreSQL, the gateway, the worker and Caddy (automatic HTTPS),
and prints the **setup token**. Running it again is safe: existing configuration and secrets are kept.

Copy `deploy/.env` and `deploy/secrets/` to a safe place **off the server** right away. Without
`cryptus_secret_key`, stored credentials (RPC keys, webhook secrets, 2FA secrets) cannot be decrypted.

## 3. First-run wizard

Open `https://pay.example.com/setup` and paste the setup token.

1. **Owner account and business details.** The business name is shown on the checkout.
2. **Networks → TON.** Backend `toncenter`, endpoint `https://toncenter.com`, paste the API key, enable it.
   *Test connection* must report healthy.
3. **Wallets.** Either
   - **A — per-order addresses:** skip this step in the wizard and continue with section 3a below, or
   - **B — straight to my wallet:** *Wallets → TON → "Straight to my wallet"*. In your wallet app tap
     **Receive**, copy the address, paste it and press *Continue*. Compare it character by character, paste it
     again and press *Verify & attach*.

USDT on TON is enabled by default. It ends up in your wallet's USDT balance either way.

## 3a. Per-order addresses, collected into your wallet

Every order gets its own deposit address (a TON wallet contract derived from the signer's key with a unique
*subwallet id*; see [WALLETS](WALLETS.md)). Once the invoice is paid, CRYPTUS collects the funds into your
main wallet automatically. The signer that holds the deposit keys runs in its own container with no
internet access and no database access. Its policy only allows payments **to your wallet**.

1. On the server, with your main wallet's address (Tonkeeper → *Receive*):

   ```bash
   cd /opt/cryptus && sudo sh deploy/signer-setup.sh UQ…your-main-wallet
   ```

   The script creates the signer's keys and prints **two recovery phrases once**. Write them on paper and
   keep them offline: they are the only way to reach funds still sitting on deposit addresses if the server
   is lost. It also writes `deploy/signer/policy.json` (allowlist = your wallet, at most 10,000 USDT
   collected per 24 h; edit to taste) and starts the signer.
2. Console → *Wallets* → **Import signer keys**. In the TON panel, choose the `Signer · ton` keychain and
   press *Attach*.
3. *Wallets → TON → Consolidation*: destination = your main wallet (the same address as in the policy),
   **Automatic planning** on. "Run every 10 min" collects soon after payment. Save.
4. Send about **1 GRAM** to the **gas wallet** shown on the TON panel. It pays the small top-up that a USDT
   deposit needs before its tokens can be moved (≈ 0.1 GRAM per USDT order, most of which returns to your
   main wallet in the same collection). GRAM deposits pay their own fee and need no top-up.
5. *Consolidation* page → switch off **Approve every sweep manually** (owner only) to make collection fully
   automatic. Leave it on if you prefer to approve each transfer.

What happens per order: the buyer pays → the invoice is `paid` → within the next run the worker plans a
sweep from the live chain balance → the signer checks it against its policy and signs → CRYPTUS follows the
transaction until the money has **arrived** in your wallet (for USDT, through both jetton wallets) and
only then marks it `confirmed`. A sweep that fails (expired message, bounce) is planned again, at most 5
times, and each failure raises a `sweep.failed` webhook.

To stop everything instantly: *Consolidation → Emergency stop*, or `touch /opt/cryptus/deploy/signer/HALT`
on the server.

## 4. Lock it down

- *Account* → enable two-factor authentication. Then *Settings → Security* → **Require 2FA for admins**.
- If you have a static IP or a VPN, restrict `/api/admin/*` to it in `deploy/Caddyfile` (an example is
  commented in the file), then run `docker compose -f deploy/docker-compose.yml --env-file deploy/.env restart caddy`.
- If you use per-order addresses, keep the signer's recovery phrases offline, and keep only a small balance
  on the gas wallet.
- Install nightly backups:
  `echo "17 3 * * * root sh /opt/cryptus/deploy/backup.sh" > /etc/cron.d/cryptus-backup`
  and copy `/var/backups/cryptus` off the server regularly.
- Optional host firewall: `ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443 && ufw enable`.

## 5. Test with a small real payment

1. *Invoices → New invoice*, for example `0.2 GRAM` or `1 USD`.
2. Open the checkout link on your phone and pay **from a different wallet**, using the QR code or
   *Open in wallet*. The amount and comment are filled in for you.
3. Within roughly 10–20 seconds the invoice goes `processing` → `paid`. The transfer appears in your
   wallet with the comment `CR-2026-000001`.
4. Repeat with USDT.

For integration tests without real money, use a **test-mode** API key with `ton-testnet` (configure the TON
Testnet network and attach a testnet wallet the same way). Testnet coins come from
[@testgiver_ton_bot](https://t.me/testgiver_ton_bot).

## 6. Connect your shop

1. *API keys* → create a **live** key. It is shown once; store it in your shop's secrets.
2. *Webhooks* → add `https://shop.example/cryptus-webhook`, then copy the signing secret.
3. In your backend, call `POST /api/v1/invoices`, redirect the buyer to `checkout_url`, and fulfil the
   order on a **verified** `invoice.paid` webhook. To be safe, re-read `GET /api/v1/invoices/{id}` before
   shipping. See [API](API.md) for signature verification.
4. **Pin your wallet address.** In direct mode every invoice pays the same address, so your shop can refuse
   any invoice whose `payment.address` is not the one you expect. Even someone who took over the gateway
   could then not redirect payments without also changing your shop:

   ```python
   EXPECTED = "UQ…your wallet…"
   if inv["payment"] and inv["payment"]["address"] != EXPECTED:
       raise RuntimeError("receiving address changed: do not show this invoice")
   ```

## 7. How direct receiving behaves

- Each invoice gets its own **comment** (memo): the invoice number, e.g. `CR-2026-000123`. If the buyer
  switches asset, the new quote gets a suffix (`CR-2026-000123-2`). The QR code and *Open in wallet*
  fill it in automatically.
- Matching ignores case and spaces, nothing else. A payment **without** the comment, with a different one,
  or with an *encrypted* comment still reaches your wallet but is **not credited** to the invoice. Find it
  in your wallet history and settle it with the buyer (refund or manual fulfilment).
- Buyers paying from an exchange must fill in the exchange's *memo/comment* field for the withdrawal.
- Payments that arrive after expiry, within the late-payment window (*Settings → Invoices*), are still
  credited and flagged `paid_late`. Later payments are not watched.
- There is nothing to consolidate, no gas wallet and no private key on the server. A compromised server
  cannot move your funds. It could only issue new invoices to a different address, which address pinning
  (above), 2FA and the audit log (`keychain.created`, `wallet.attached`) guard against.
- Your wallet can still be used for anything else. Transfers without a CRYPTUS comment are ignored.

## 8. Day-to-day operations

| Task | Command (from `/opt/cryptus`) |
|---|---|
| Logs | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f web worker` |
| Update | `git pull && docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build` |
| Uptime check | monitor `https://pay.example.com/healthz` (expects HTTP 200) |
| Audit chain | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec web cryptus audit-verify` |
| Lost password | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec web cryptus reset-password you@example.com` |

**Restore** on a new server: install as above, stop `web` and `worker`, restore `deploy/.env` and
`deploy/secrets/` from `config-*.tgz`, then load the dump and start everything again:

```bash
C="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
$C stop web worker
$C exec -T postgres pg_restore -U cryptus -d cryptus --clean --if-exists < cryptus-YYYYMMDDTHHMMSSZ.dump
$C up -d
```

## 9. Before you rely on it

- Read [COMPLIANCE](COMPLIANCE.md). Self-hosting does not remove legal obligations such as licensing,
  tax reporting or sanctions screening where they apply to you.
- Read the *Known limitations* in [SECURITY](SECURITY.md).
- Subscribe a monitoring endpoint to `invoice.needs_review` and `payment.orphaned` webhooks.
