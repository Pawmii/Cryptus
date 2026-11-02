# Security

## Threat model (summary)

| Threat | Controls |
|---|---|
| Web tier compromise | No private keys on the web tier. The signer enforces destinations, caps and rate limits independently. The emergency stop exists on both sides |
| Database tampering | Hash-chained audit log (`cryptus audit-verify`). Sweeps re-read chain state. Secrets at rest are encrypted (Fernet, key derived via HKDF from `CRYPTUS_SECRET_KEY`) |
| Address substitution | Deterministic derivation from verified public material. Operator verification of address #0. Unique constraints on `(keychain, index)` and `(network, address)` |
| Fake payments | On-chain re-verification of every payment (receipt status, log contents, canonical block, jetton wallet owner/master) |
| Reorgs / RBF | Configurable confirmations. Re-verification until 3× depth. Orphaned payments revert invoices |
| Fresh-install takeover | One-time setup token required until an owner exists |
| Credential stuffing | Argon2id hashing, per-IP and per-account throttling, lockout, optional/enforced TOTP |
| CSRF / XSS | SameSite=Strict, HttpOnly cookies, per-session CSRF token header, strict CSP (`script-src 'self'`), no inline scripts |
| SSRF via webhooks | HTTPS only in production. Private, loopback and link-local targets refused. No redirects |
| API key leakage | Keys stored as keyed HMAC digests, scoped, live/test separation, instant revocation |
| Replay to signer | HMAC over timestamp + body, 60 s window, single-use request ids |

## Hardening checklist

- [ ] Set `CRYPTUS_SECRET_KEY` (≥ 32 random chars) through a secret file and back it up. Losing it makes stored credentials unreadable.
- [ ] Serve only over HTTPS (`CRYPTUS_PUBLIC_URL=https://…`, Caddy does this). Cookies are `Secure` by default.
- [ ] Set `CRYPTUS_TRUSTED_PROXIES` to your proxy's network only.
- [ ] Enforce 2FA for admins (*Settings → Security*).
- [ ] Restrict `/api/admin/*` to your office or VPN IPs at the proxy if possible (see `deploy/Caddyfile`).
- [ ] Prefer your own nodes (Bitcoin Core, EVM RPC, TON). Public endpoints learn which addresses you watch.
- [ ] Run the signer on a separate host with no inbound internet. Keep its policy file read-only to the gateway operators.
- [ ] Keep a small balance on gas wallets. Set daily limits in the signer policy.
- [ ] Back up PostgreSQL, `deploy/secrets/`, and the signer's keystore and recovery phrases (separately and offline).
- [ ] Monitor `invoice.needs_review`, `payment.orphaned` and `sweep.failed` webhooks.

## Reporting vulnerabilities

Please report security issues privately to the maintainers rather than opening a public issue.
