# Security

## Threat model (summary)

| Threat | Controls |
|---|---|
| Web tier compromise | No private keys on the web tier. The signer enforces destinations, caps and rate limits independently. The emergency stop exists on both sides |
| Database tampering | Hash-chained audit log (`cryptus audit-verify`). Sweeps re-read chain state. Secrets at rest are encrypted (Fernet, key derived via HKDF from `CRYPTUS_SECRET_KEY`) |
| Address substitution | Deterministic derivation from verified public material. Operator verification of address #0. Unique constraints on `(keychain, index)` and `(network, address)` |
| Fake payments | On-chain re-verification of every payment (receipt status, log contents, canonical block, jetton wallet owner/master) |
| Spoofed memos (TON direct) | Only plain-text comments that equal an open deposit's memo count, and only if sent after the memo was issued. Fake jettons never reach the merchant's genuine jetton wallet. The value must be credited (no bounce). Shops can pin the constant receiving address |
| Reorgs / RBF | Configurable confirmations. Re-verification until 3× depth. Orphaned payments revert invoices |
| Fresh-install takeover | One-time setup token required until an owner exists |
| Credential stuffing | Argon2id hashing, per-IP and per-account throttling, temporary lockout, single-use TOTP codes. *Settings → Security → Require 2FA* blocks privileged actions for admins/owners until they enrol |
| Misconfiguration lock-out | Settings are type- and range-checked on write (e.g. session lifetime 1–720 h); `cryptus reset-password` from a shell as last resort |
| Lost broadcast responses | The id of every signed transaction is recorded before broadcast; a retry first checks whether it already landed instead of signing again |
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

## Known limitations

* Webhook targets are resolved and checked before connecting. A DNS answer that changes between the check
  and the connection (DNS rebinding) is not prevented. Use HTTPS endpoints you control.
* The account lockout (8 failures → 15 min) can be triggered by anyone who knows an operator's email. Restrict
  the console to trusted networks at the proxy if that matters to you.
* The merchant API has no built-in rate limit. Put one in front of `/api/v1` at the proxy if keys may leak.
* TON direct mode credits only transfers that carry the invoice's comment. Payments without it reach the
  merchant's wallet but stay unattributed until the merchant resolves them with the payer.
* With `deploy/signer-setup.sh` the signer runs on the same host as the gateway, isolated by container and
  network (no internet, no database). For stronger isolation, move it to a separate machine (see OPERATIONS).
* TON native sweeps send the whole wallet balance. The signer's daily limits apply to jetton amounts and
  top-ups, not to that balance, which is still bounded by the allowlisted destination.

## Reporting vulnerabilities

Please report security issues privately to the maintainers rather than opening a public issue.
