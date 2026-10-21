#!/bin/sh
set -eu
if [ -n "${BROWSER_CA_FILE:-}" ]; then
  cert_dir=/home/career/.pki/nssdb
  mkdir -p "$cert_dir"
  if [ ! -f "$cert_dir/cert9.db" ]; then certutil -N -d "sql:$cert_dir" --empty-password; fi
  certutil -A -d "sql:$cert_dir" -n current-environment-proxy -t 'C,,' -i "$BROWSER_CA_FILE"
fi
if [ "${BROWSER_HEADLESS:-false}" = false ]; then
  exec xvfb-run -a -s '-screen 0 1280x900x24 -nolisten tcp' "$@"
fi
exec "$@"
