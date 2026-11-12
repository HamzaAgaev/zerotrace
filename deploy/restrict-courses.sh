#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run with sudo bash deploy/restrict-courses.sh' >&2
  exit 1
fi
config=/etc/nginx/conf.d/google-drive-server.conf
courses=/etc/nginx/snippets/courses.conf
backup_dir="$(mktemp -d /etc/nginx/zerotrace-routes-backup.XXXXXX)"
cp -p "$config" "$backup_dir/host.conf"
cp -p "$courses" "$backup_dir/courses.conf"
restore() {
  cp -p "$backup_dir/host.conf" "$config"
  cp -p "$backup_dir/courses.conf" "$courses"
  echo "Configuration restored; backup: $backup_dir" >&2
}
trap restore ERR
python3 deploy/restrict-courses.py "$config" "$courses"
nginx -t
systemctl reload nginx
trap - ERR
echo "Courses are available only under /courses/. Root and unknown paths return 404. Backup: $backup_dir"
