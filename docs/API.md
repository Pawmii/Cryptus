# Merchant API (v1)

Base URL: `https://<your-domain>/api/v1`. Interactive reference: `/api/docs`.

## Authentication

```
Authorization: Bearer ck_live_xxxxxxxx_…      (live: mainnet assets)
Authorization: Bearer ck_test_xxxxxxxx_…      (test: testnet assets only)
```

Scopes: `invoices:read`, `invoices:write`. Keys are shown once at creation.

## Create an invoice

`POST /invoices` (supports `Idempotency-Key`; replays return the original invoice, while a different body under
the same key returns `409 idempotency_conflict`).

| Field | Type | Notes |
|---|---|---|
| `amount` | string | Decimal string. Fiat: ≤ 2 decimals. Crypto: ≤ asset decimals |
| `currency` | string | ISO-4217 (`USD`, `EUR`, …) **or** a canonical asset id (`ton:jetton:0:b113…`) |
| `asset` | string? | Quote immediately in this asset (address allocated now) |
| `allowed_assets` | string[]? | Restrict payer choice. Default: all operational assets |
| `order_id` | string? | Your reference (searchable) |
| `description` | string? | Shown to the payer |
| `metadata` | object? | Returned verbatim, never shown to the payer (≤ 4 KB) |
| `expires_in_minutes` | int? | 5 – 10080. Default from settings |
| `redirect_url` | string? | Payer is offered this link once paid |

Response (abridged):

```json
{
  "id": "6f0c…", "number": "CR-2026-000001", "status": "pending", "mode": "live",
  "price": {"amount": "25.00", "currency": "USD"},
  "payment": {
    "asset": "ton:jetton:0:b113a994…", "symbol": "USDT", "network": "ton",
    "address": "UQB…", "amount_due": "25", "amount_due_atomic": "25000000",
    "confirmations_required": 1, "payment_uri": "ton://transfer/UQB…?jetton=EQCx…&amount=25000000"
  },
  "checkout_url": "https://pay.example.com/pay/6f0c…",
  "expires_at": "2026-11-02T21:30:00+00:00"
}
```

Other endpoints: `GET /invoices/{id}`, `GET /invoices?status=paid&order_id=…`, `POST /invoices/{id}/cancel`,
`GET /assets` (assets operational for your key's mode).

Errors use `{"detail": {"code": "...", "message": "..."}}` with codes such as `no_assets`,
`asset_unavailable`, `rate_unavailable` (503), `amount_limit`, `invoice_closed`, `idempotency_conflict`.

## Statuses

`pending` → `processing` → `paid`, or `expired` / `underpaid` / `cancelled`. Flags: `overpaid`,
`paid_late`, `needs_review`. Ship goods on **`paid`** only.

## Webhooks

Events: `invoice.created|processing|paid|underpaid|expired|cancelled|reverted|needs_review`,
`payment.detected|confirmed|orphaned`, `sweep.broadcast|confirmed|failed`.

```
POST /your/endpoint
Cryptus-Event: invoice.paid
Cryptus-Delivery: 9c1e…
Cryptus-Signature: t=1793660000,v1=5b0f…   # hex HMAC-SHA256(secret, f"{t}.{raw_body}")

{"id": "evt…", "type": "invoice.paid", "created_at": "…", "mode": "live", "data": {"invoice": {…}}}
```

* Verify the signature over the **raw** body in constant time, and reject timestamps older than 5 minutes.
* Delivery is at-least-once. De-duplicate on `id`.
* Respond 2xx within 10 s. Failures retry at 30 s, 2 m, 10 m, 30 m, 1 h, 3 h, 6 h, 12 h, 24 h, then `dead`
  (redeliverable from the console).
* Treat `invoice.reverted` (deep reorg after payment) as a reversal of fulfilment eligibility.
