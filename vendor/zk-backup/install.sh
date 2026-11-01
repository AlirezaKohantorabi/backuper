#!/usr/bin/env bash
# install.sh - run zk_backup.py every night on a Linux host via a systemd timer.
#
#   sudo ./install.sh <device-ip> [extra zk_backup.py options]
#   e.g.  sudo ./install.sh 192.168.10.50 --udp --password 1234
#
# Run it from the folder that contains zk_backup.py. Safe to re-run (it updates in place).
set -euo pipefail

IP="${1:?usage: sudo ./install.sh <device-ip> [extra zk_backup.py options]}"
shift
OPTS="$*"

APP=/opt/zk-backup            # script + its own Python environment
DATA=/var/backups/zk          # where backups land
CONF=/etc/default/zk-backup   # device IP + options; edit this later, not the unit
SVC_USER=zkbackup

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
[[ -f zk_backup.py ]] || { echo "run from the folder containing zk_backup.py"; exit 1; }

# 1. Unprivileged system user: the backup never needs root.
id "$SVC_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"

# 2. Script + isolated venv pinned to the pyzk version the read-only guard was tested against.
install -d -m 755 "$APP"
install -m 644 zk_backup.py "$APP/zk_backup.py"
if [[ ! -x "$APP/venv/bin/python" ]]; then
    python3 -m venv "$APP/venv" || { echo "venv failed - Debian/Ubuntu: apt install python3-venv"; exit 1; }
fi
"$APP/venv/bin/pip" install --quiet --disable-pip-version-check "pyzk==0.9"

# 3. Backup folder only the service user can read (users.csv holds PINs and card numbers).
install -d -m 700 -o "$SVC_USER" -g "$SVC_USER" "$DATA"

# 4. Settings. Root-only, because a Comm Key may be in here.
(umask 077; cat > "$CONF" <<EOF
ZK_IP="$IP"
ZK_OPTS="$OPTS"
EOF
)

# 5. The job. Type=oneshot: run, exit; a non-zero exit marks the unit "failed".
cat > /etc/systemd/system/zk-backup.service <<EOF
[Unit]
Description=ZKTeco attendance terminal backup (read-only)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$SVC_USER
Group=$SVC_USER
EnvironmentFile=$CONF
Environment=PYTHONDONTWRITEBYTECODE=1
# \$ZK_OPTS unbraced = split into separate arguments; empty = no arguments
ExecStart=$APP/venv/bin/python $APP/zk_backup.py \${ZK_IP} --out $DATA \$ZK_OPTS
TimeoutStartSec=20min
# Sandbox: read-only view of the OS, writable only in the backup folder
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=$DATA
EOF

# 6. The schedule. Persistent=true: if the host was off at 02:17, run at next boot.
cat > /etc/systemd/system/zk-backup.timer <<EOF
[Unit]
Description=Nightly ZKTeco attendance backup

[Timer]
OnCalendar=*-*-* 02:17:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now zk-backup.timer

# 7. First backup right now, so you see it work (or why it doesn't).
echo "Running a first backup..."
if systemctl start zk-backup.service; then
    echo "First backup OK."
else
    echo "First backup FAILED - log below. Fix $CONF, then: sudo systemctl start zk-backup.service"
fi
journalctl -u zk-backup.service -n 25 --no-pager -o cat
echo
systemctl list-timers zk-backup.timer --no-pager
echo "Backups: $DATA   Log: $DATA/backup.log   Settings: $CONF"
