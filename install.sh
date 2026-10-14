#!/usr/bin/env bash
# Backuper installer — run from the repository folder:
#   ./install.sh              install/upgrade + systemd service (asks for sudo once)
#   ./install.sh --no-service install/upgrade only (run with: .venv/bin/backuper serve)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="backuper"
RUN_USER="$(id -un)"
WITH_SERVICE=1
[[ "${1:-}" == "--no-service" ]] && WITH_SERVICE=0

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

cd "$APP_DIR"

# 1. uv (Python package manager; no root needed, works without python3-venv/pip)
if ! command -v uv >/dev/null 2>&1; then
  if [[ -x "$HOME/.local/bin/uv" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    say "Installing uv into ~/.local/bin"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

# 2. virtualenv + dependencies (uv downloads Python 3.12 itself if the system has none)
say "Creating virtualenv and installing dependencies"
uv venv --quiet --allow-existing --python 3.12 .venv
uv pip install --quiet --python .venv/bin/python -e .

# 3. .env with a fresh encryption key
if [[ ! -f .env ]]; then
  say "Creating .env"
  cp .env.example .env
  KEY="$(.venv/bin/backuper gen-key)"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${KEY}|" .env
  chmod 600 .env
  warn "Fill in BOT_TOKEN, API_ID, API_HASH and OWNER_IDS in $APP_DIR/.env"
else
  say ".env already exists — left untouched"
fi
mkdir -p data && chmod 700 data

# 3b. `backuper` on the PATH for this user (~/.local/bin is on Ubuntu's default PATH), works from any folder
mkdir -p "$HOME/.local/bin"
ln -sfn "$APP_DIR/.venv/bin/backuper" "$HOME/.local/bin/backuper"

# 4. systemd service
if [[ $WITH_SERVICE -eq 1 ]]; then
  say "Installing systemd service '${SERVICE_NAME}' (runs as ${RUN_USER}) — sudo needed"
  UNIT="$(sed -e "s|@USER@|${RUN_USER}|g" -e "s|@DIR@|${APP_DIR}|g" deploy/backuper.service)"
  echo "$UNIT" | sudo tee /etc/systemd/system/${SERVICE_NAME}.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable ${SERVICE_NAME} >/dev/null
  if grep -qE '^BOT_TOKEN=.+' .env && grep -qE '^API_ID=.+' .env; then
    sudo systemctl restart ${SERVICE_NAME}
    say "Service started. Logs: journalctl -u ${SERVICE_NAME} -f"
  else
    warn "Service installed but not started (fill .env first, then: sudo systemctl restart ${SERVICE_NAME})"
  fi
fi

say "Done. Useful commands:"
cat <<EOF
  backuper check                 # config + archive access
  backuper import my-company.yaml # bulk-load sources/credentials
  backuper test                  # test every source
  backuper status                # what's running now, last and next runs
  backuper restart               # restart the service (after editing .env or updating) — no sudo
EOF
