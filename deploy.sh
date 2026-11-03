#!/bin/bash
set -euo pipefail

# === Configuration ===
LOCAL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/"
# Optional private, shell-format defaults kept outside Git.
if [[ -f "${LOCAL_DIR}.deploy.env" ]]; then
    source "${LOCAL_DIR}.deploy.env"
fi
SERVER="${DEPLOY_SERVER:?Set DEPLOY_SERVER to your SSH user@host}"
REMOTE_DIR="${DEPLOY_DIR:-/opt/myblog_py}"
SERVICE="${DEPLOY_SERVICE:-myblog_py.service}"
if [[ ! "$SERVICE" =~ ^[a-zA-Z0-9_.@-]+\.service$ ]]; then
    echo "Invalid DEPLOY_SERVICE" >&2
    exit 1
fi

echo "📦 Syncing project to ${SERVER}:${REMOTE_DIR} ..."

# rsync transfers only changed files. Excluded server files are also protected
# from --delete; do not add --delete-excluded.
rsync -az --itemize-changes --delete \
    --exclude '__pycache__' \
    --exclude '.git' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude '.local/' \
    --exclude '.deploy.env*' \
    --exclude 'note.md' \
    --exclude '.DS_Store' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache/' \
    --exclude 'venv' \
    --exclude '.venv' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude '*.pid' \
    --exclude '*.log' \
    --exclude '*.duckdb*' \
    --exclude '*.pem' \
    --exclude '*.key' \
    --exclude '*.crt' \
    --exclude '*.p12' \
    --exclude '*.pfx' \
    --exclude '*.csr' \
    --exclude '.qwen/' \
    --exclude '.idea/' \
    --exclude '.vscode/' \
    --exclude 'tempCA/' \
    --exclude 'certBlueNest/' \
    --exclude '*.md' \
    --exclude 'tests/' \
    --exclude 'deployment/' \
    --exclude 'deploy.sh' \
    --exclude 'init_db.py' \
    --exclude 'add_api_secrets_to_users.py' \
    --exclude 'package-lock.json' \
    "${LOCAL_DIR}" "${SERVER}:${REMOTE_DIR}/"

echo ""
echo "✅ Files synced!"
echo ""

# Allocate a terminal so sudo can prompt without storing a password.
read -r -p "🔄 Restart ${SERVICE} on server? [y/N] " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    ssh -t "${SERVER}" "sudo /usr/bin/systemctl restart ${SERVICE} && /usr/bin/systemctl status ${SERVICE} --no-pager --full"
    echo "✅ Service restarted through systemd"
else
    echo "⏭️  Skipped restart."
fi

echo ""
echo "🎉 Deploy complete!"
