#!/bin/sh
# One-command production install on a fresh Ubuntu 22.04+/Debian 12 server.
#
#   git clone https://github.com/Pawmii/Cryptus.git /opt/cryptus
#   cd /opt/cryptus && sudo sh deploy/install.sh pay.example.com
#
# Before running: point the domain's DNS A/AAAA record at this server and open TCP 80 and 443
# (Caddy obtains the TLS certificate on first start). Re-running is safe: existing .env and secrets are kept.
set -eu

DOMAIN="${1:-}"
[ -n "$DOMAIN" ] || { echo "usage: sudo sh deploy/install.sh <domain>   e.g. pay.example.com"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.yml --env-file .env"

say() { printf '\n\033[1;31m▸\033[0m %s\n' "$*"; }
rand() { head -c 48 /dev/urandom | base64 | tr -d '\n=+/' | cut -c1-40; }

say "Docker"
command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl ca-certificates; }
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || { echo "the docker compose plugin is missing"; exit 1; }

say "DNS check for $DOMAIN"
ME=$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || true)
DNS=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)
if [ -n "$ME" ] && [ "$ME" != "$DNS" ]; then
  echo "  warning: $DOMAIN resolves to '${DNS:-nothing}', this server is $ME."
  echo "  Caddy cannot obtain a certificate until DNS points here. Continuing anyway."
else
  echo "  $DOMAIN → ${DNS:-?}"
fi

say "Configuration (deploy/.env)"
if [ ! -f .env ]; then
  sed -e "s|^CRYPTUS_DOMAIN=.*|CRYPTUS_DOMAIN=$DOMAIN|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand)|" \
      -e "s|^BITCOIND_RPC_PASSWORD=.*|BITCOIND_RPC_PASSWORD=$(rand)|" .env.example > .env
  chmod 600 .env
  echo "  created"
else
  grep -q "^CRYPTUS_DOMAIN=$DOMAIN\$" .env || echo "  kept existing .env (CRYPTUS_DOMAIN there differs from $DOMAIN — edit it if needed)"
fi

say "Secrets (deploy/secrets)"
CRYPTUS_NONINTERACTIVE=1 sh ./init-secrets.sh
mkdir -p signer && chown -R 10001:10001 signer   # the signer container runs as uid 10001 (unused unless enabled)

say "Building and starting (first build takes a few minutes)"
$COMPOSE up -d --build

say "Waiting for the gateway"
i=0
until $COMPOSE exec -T web python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status!=200)" 2>/dev/null; do
  i=$((i + 1)); [ $i -lt 90 ] || { echo "  the gateway did not become healthy — see: $COMPOSE logs web"; exit 1; }
  sleep 2
done
TOKEN=$($COMPOSE exec -T web sh -c 'cat /data/setup-token 2>/dev/null' || true)
[ -n "$TOKEN" ] || TOKEN="(none — setup was already completed)"

cat <<EOF

  CRYPTUS is running.

  1. Open   https://$DOMAIN/setup
  2. Setup token: $TOKEN
  3. Follow docs/PRODUCTION.md (TON wallet, 2FA, backups).

  Back up deploy/secrets/ and deploy/.env now, off this server. Without cryptus_secret_key the stored
  credentials cannot be decrypted.
  Nightly backups:  echo "17 3 * * * root sh $(pwd)/backup.sh" > /etc/cron.d/cryptus-backup
EOF
