#!/bin/sh
# Starts the demo: makes an access key on first start, loads the demo alerts once, then serves the console.
set -eu
DATA="${ERP_AGENTS_DATA:-/data}"
CONFIG="${ERP_AGENTS_CONFIG:-/app/docker/settings.demo.yaml}"
export ERP_AGENTS_DATA="$DATA"

if [ -z "${ERP_AGENTS_OWNER_TOKEN:-}" ]; then
  if [ ! -s "$DATA/owner_token" ]; then
    ( umask 077; python -c "import secrets; print(secrets.token_urlsafe(32))" > "$DATA/owner_token" )
  fi
  ERP_AGENTS_OWNER_TOKEN="$(cat "$DATA/owner_token")"
  export ERP_AGENTS_OWNER_TOKEN
fi

if [ ! -e "$DATA/.demo_loaded" ]; then
  erp-agents --config "$CONFIG" run-once > "$DATA/first-run.log" 2>&1 || true
  touch "$DATA/.demo_loaded"
fi

echo "======================================================================"
echo " Stock Assistant demo is starting."
echo " Open http://127.0.0.1:8100/  and enter this access key:"
echo "   $ERP_AGENTS_OWNER_TOKEN"
echo " (demo data only; nothing is sent anywhere)"
echo "======================================================================"
exec erp-agents --config "$CONFIG" serve
