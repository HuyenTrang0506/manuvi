#!/bin/bash
# Run once by UserData (as root) after the source tarball is extracted to /opt/manuvi/src.
# Installs the compose plugin (checksum verified), then a systemd unit that starts the stack
# on every boot, so stop/start of the instance just works.
set -euo pipefail

COMPOSE_VERSION="v2.29.7"
plugin_dir=/usr/local/lib/docker/cli-plugins
mkdir -p "$plugin_dir"
bin="docker-compose-linux-$(uname -m)"
base="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}"
tmp="$(mktemp -d)"
curl -fsSL -o "$tmp/$bin" "$base/$bin"
curl -fsSL -o "$tmp/$bin.sha256" "$base/$bin.sha256"
(cd "$tmp" && sha256sum -c "$bin.sha256")
install -m 0755 "$tmp/$bin" "$plugin_dir/docker-compose"
rm -rf "$tmp"

cat > /etc/systemd/system/manuvi.service <<'UNIT'
[Unit]
Description=Manuvi web stack
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash /opt/manuvi/src/deploy/start.sh up
ExecStop=/bin/bash /opt/manuvi/src/deploy/start.sh down
TimeoutStartSec=900

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now manuvi.service
