#!/bin/sh
# Enable managed signing: a unique TON deposit address per order, collected automatically into your wallet.
#
#   sudo sh deploy/signer-setup.sh UQ…your-main-ton-wallet
#
# Creates the signer's own keys (a separate seed — NOT your wallet's), a policy that only ever pays your wallet,
# and starts the signer next to the gateway. Run deploy/install.sh first. Safe to re-run: existing keys and
# policy are kept.
set -eu

WALLET="${1:-}"
[ -n "$WALLET" ] || { echo "usage: sudo sh deploy/signer-setup.sh <your main TON wallet address>"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
cd "$(dirname "$0")"
[ -f .env ] && [ -d secrets ] || { echo "run deploy/install.sh first"; exit 1; }
COMPOSE="docker compose -f docker-compose.yml --env-file .env --profile managed-signing"

say() { printf '\n\033[1;31m▸\033[0m %s\n' "$*"; }

say "Checking the destination address"
$COMPOSE run --rm --no-deps -T signer python -c "
import sys
from cryptus.core.derivation import parse_ton_wallet
from cryptus.core.networks import NETWORKS
a = parse_ton_wallet(sys.argv[1], NETWORKS['ton'])
print('  ok:', a.to_str(is_user_friendly=True, is_url_safe=True, is_bounceable=False))
" "$WALLET"

say "Signer policy (deploy/signer/policy.json)"
mkdir -p signer
if [ ! -f signer/policy.json ]; then
  cat > signer/policy.json <<EOF
{
  "halted": false,
  "networks": {
    "ton": {
      "allowlist": ["$WALLET"],
      "max_gas_topup": "0.2",
      "max_jetton_attach": "0.1",
      "daily_limits": { "ton:jetton:*": "10000000000" }
    }
  }
}
EOF
  echo "  created: sweeps may only pay $WALLET; at most 10,000 USDT per 24 h (edit daily_limits to change)"
else
  echo "  kept existing policy — make sure its TON allowlist contains $WALLET"
fi
chown -R 10001:10001 signer   # the signer container runs as uid 10001
chmod 600 signer/policy.json

if [ ! -f signer/keystore.json ]; then
  say "Creating the signer keys"
  $COMPOSE run --rm --no-deps signer cryptus-signer init --keystore /signer/keystore.json
  printf '\n  Write BOTH recovery phrases above on paper and keep them offline.\n'
  printf '  They are the only way to recover funds sitting on deposit addresses. Press Enter when done. '
  read -r _
  clear 2>/dev/null || true
else
  echo "  keystore exists — keeping it"
fi

say "Connecting the gateway to the signer"
grep -q '^CRYPTUS_SIGNER_URL=' .env || echo 'CRYPTUS_SIGNER_URL=http://signer:8700' >> .env
if ! grep -q '^COMPOSE_PROFILES=' .env; then
  echo 'COMPOSE_PROFILES=managed-signing' >> .env
elif ! grep -q '^COMPOSE_PROFILES=.*managed-signing' .env; then  # keep profiles already enabled (e.g. bitcoind)
  sed -i -e 's/^COMPOSE_PROFILES=\(..*\)$/COMPOSE_PROFILES=\1,managed-signing/' \
         -e 's/^COMPOSE_PROFILES=$/COMPOSE_PROFILES=managed-signing/' .env
fi
$COMPOSE up -d

cat <<EOF

  The signer is running. In the console:
    1. Wallets → "Import signer keys", then attach the "Signer · ton" keychain to TON.
    2. Wallets → TON → Consolidation: destination $WALLET, enable "Automatic planning".
    3. Send ~1 GRAM to the TON gas wallet shown there (pays for USDT collection).
    4. Consolidation → switch off "Approve every sweep manually" for fully automatic collection.
  Details: docs/PRODUCTION.md, "Per-order addresses".
EOF
