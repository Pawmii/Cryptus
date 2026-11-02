# Compliance

CRYPTUS is software. Operating it to accept payments is a business activity that may be regulated
where you or your customers are located. **Self-hosting does not remove legal obligations.**

## What CRYPTUS does

* **Data minimisation.** The checkout collects no payer names, emails, IP-derived locations or cookies.
  The only identifier is the unguessable invoice id. Operator activity *is* logged (IP, user agent) for
  security, and you are the data controller for those logs.
* **No mandatory KYC module.** None is bundled. If your obligations require identity checks, perform them
  in your own store before creating an invoice, and reference them via `metadata`.
* **Configurable policies** (*Settings → Policies & compliance*):
  * terms, privacy and refund-policy links shown on every checkout,
  * mandatory terms acceptance before an address is revealed,
  * a checkout notice for regulatory disclosures,
  * a maximum invoice amount,
  * an internal jurisdiction note.
* **Auditability.** A tamper-evident audit log of operator actions, CSV export of invoices with
  on-chain references, and permanent links from each payment to its transaction.

## What remains your responsibility

* Determining whether you need registration or a licence (e.g. as a crypto-asset service provider or
  money transmitter).
* Tax treatment and reporting of crypto receipts, including the fiat value at the time of payment
  (CRYPTUS stores the quoted rate and its source for each deposit).
* Sanctions and AML screening appropriate to your risk profile. Integrate a screening provider in your
  store or in a webhook consumer if required.
* Consumer protection: refunds, disclosures, and the handling of over- and under-payments flagged by
  CRYPTUS.
* Data-protection duties for operator logs and anything you put in invoice `metadata`.

Seek qualified legal advice for each jurisdiction you operate in.
