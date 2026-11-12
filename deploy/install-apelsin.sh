#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo bash deploy/install-apelsin.sh" >&2
  exit 1
fi

config=/etc/nginx/conf.d/google-drive-server.conf
snippet=/etc/nginx/snippets/zerotrace.conf
test -f "$config"
grep -q 'server_name mhs.dmitriy.space;' "$config"
grep -q 'include /etc/nginx/snippets/folio.conf;' "$config"

docker compose -f deploy/apelsin.compose.yaml up -d --build --remove-orphans --wait --wait-timeout 180
curl --fail --silent http://127.0.0.1:8000/healthz

backup="${config}.zerotrace-backup-$(date +%Y%m%d%H%M%S)"
cp -p "$config" "$backup"
snippet_backup="$(mktemp)"
had_snippet=false
if [ -f "$snippet" ]; then
  cp -p "$snippet" "$snippet_backup"
  had_snippet=true
fi
install -m 644 deploy/zerotrace.nginx.conf "$snippet"
python3 - "$config" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
line = '  include /etc/nginx/snippets/zerotrace.conf;'
if line not in s:
    anchor = '  include /etc/nginx/snippets/folio.conf;'
    if s.count(anchor) != 1:
        raise SystemExit('Expected exactly one folio include; inspect nginx config.')
    p.write_text(s.replace(anchor, anchor + '\n' + line))
PY
if nginx -t && systemctl reload nginx; then
  rm -f "$snippet_backup"
  curl --fail --silent --show-error --resolve mhs.dmitriy.space:443:127.0.0.1 https://mhs.dmitriy.space/zerotrace/healthz
  echo
  echo 'ZeroTrace: https://mhs.dmitriy.space/zerotrace/'
else
  cp -p "$backup" "$config"
  if "$had_snippet"; then cp -p "$snippet_backup" "$snippet"; else rm -f "$snippet"; fi
  rm -f "$snippet_backup"
  echo 'Nginx configuration restored from backup.' >&2
  exit 1
fi
