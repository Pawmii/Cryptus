#!/bin/sh
# Generate secret files for docker compose. Never commit deploy/secrets/. Existing files are kept.
set -eu
cd "$(dirname "$0")"
mkdir -p secrets
umask 077
gen() { [ -s "secrets/$1" ] || head -c 48 /dev/urandom | base64 | tr -d '\n=+/' > "secrets/$1"; }
gen cryptus_secret_key
gen signer_token
if [ ! -s secrets/signer_passphrase ]; then
  p=""
  if [ -t 0 ] && [ -z "${CRYPTUS_NONINTERACTIVE:-}" ]; then
    echo "Passphrase for the signer keystore (16+ chars; leave empty to generate one — only used with managed signing):"
    stty -echo; read -r p; stty echo; echo
  fi
  if [ -n "$p" ]; then printf '%s' "$p" > secrets/signer_passphrase; else gen signer_passphrase; fi
fi
echo "Secrets in deploy/secrets/ (mode 600). Back up cryptus_secret_key: it decrypts stored credentials."
