#!/bin/sh
# Generate secret files for docker compose. Never commit deploy/secrets/.
set -eu
cd "$(dirname "$0")"
mkdir -p secrets
umask 077
gen() { [ -s "secrets/$1" ] || head -c 48 /dev/urandom | base64 | tr -d '\n=+/' > "secrets/$1"; }
gen cryptus_secret_key
gen signer_token
[ -s secrets/signer_passphrase ] || { echo "Enter a passphrase for the signer keystore (16+ chars):"; stty -echo; read -r p; stty echo; printf '%s' "$p" > secrets/signer_passphrase; }
echo "Secrets written to deploy/secrets/ (mode 600). Back up cryptus_secret_key: it decrypts stored credentials."
