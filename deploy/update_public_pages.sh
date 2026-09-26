#!/bin/bash
# Publish only the static support/terms pages from a pinned repository revision.
set -euo pipefail
commit=${1:-}
[[ $EUID -eq 0 && $commit =~ ^[0-9a-f]{40}$ ]] || exit 1
config=$(readlink -f /etc/apache2/sites-enabled/000-00-rail-action.conf)
backup=$(mktemp -d /opt/rtt-public-pages-backup.XXXXXX)
cp -a "$config" "$backup/apache.conf"
root=/var/www/rail-public
mkdir -p "$root/releases"
release="$root/releases/$commit"
test ! -e "$release"
mkdir "$release"
curl -fsSL "https://github.com/mikegtn/RTT-Python-GPT-Action/archive/$commit.tar.gz" -o "$backup/source.tar.gz"
tar -xzf "$backup/source.tar.gz" --strip-components=1 -C "$release"
mkdir "$release/public/licenses"
cp "$release/LICENSE" "$release/public/licenses/gpl-3.0.txt"
cp "$release/public/leaflet-license.txt" "$release/public/licenses/leaflet.txt"
previous=$(readlink "$root/current" || true)
rollback() {
  status=$?
  if [[ $status -ne 0 ]]; then
    cp -a "$backup/apache.conf" "$config"
    if [[ -n $previous ]]; then ln -sfn "$previous" "$root/current"; fi
    apache2ctl configtest && systemctl reload apache2 || true
  fi
  exit "$status"
}
trap rollback EXIT
ln -sfn "$release/public" "$root/current"
python3 - "$config" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); text=p.read_text()
if '# TrainBrain public pages' not in text:
    marker='    ProxyPass /mcp '
    assert text.count(marker)==1
    block='''    # TrainBrain public pages
    ProxyPass /support !
    ProxyPass /terms !
    ProxyPass /licenses/ !
    Alias /support /var/www/rail-public/current/support-terms.html
    Alias /terms /var/www/rail-public/current/support-terms.html
    Alias /licenses/ /var/www/rail-public/current/licenses/
    <Directory /var/www/rail-public>
        Options -Indexes +FollowSymLinks
        Require all granted
        AddDefaultCharset UTF-8
    </Directory>
'''
    text=text.replace(marker,block+marker)
    p.write_text(text)
PY
apache2ctl configtest
systemctl reload apache2
curl -fsS https://rail.mikegtn.net/support -o "$backup/live.html"
cmp "$release/public/support-terms.html" "$backup/live.html"
curl -fsS https://rail.mikegtn.net/terms -o "$backup/terms.html"
cmp "$backup/live.html" "$backup/terms.html"
echo "Published $commit; backup $backup"
