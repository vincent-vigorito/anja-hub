#!/bin/bash
# launch_dashboard.sh — avvia anja Mission Control
#
# Uso:
#   ./launch_dashboard.sh <hub-path> [port]
#
# Esempi:
#   ./launch_dashboard.sh ~/Documents/TEST-HUB
#   ./launch_dashboard.sh ~/Documents/TEST-HUB 8765
#
# Apre automaticamente il browser dopo 1 secondo.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB="${1:-}"
PORT="${2:-8765}"

if [ -z "$HUB" ]; then
  echo "Usage: $0 <hub-path> [port]"
  echo ""
  echo "Esempio: $0 ~/Documents/TEST-HUB"
  exit 1
fi

# expand tilde
HUB="${HUB/#\~/$HOME}"

if [ ! -d "$HUB" ]; then
  echo "ERROR: hub directory non trovata: $HUB"
  exit 1
fi

if [ ! -f "$HUB/config/projects.json" ]; then
  echo "ERROR: $HUB non sembra un hub anja (manca config/projects.json)"
  exit 1
fi

URL="http://127.0.0.1:$PORT"

# Apri il browser dopo 1.5s (in background)
(sleep 1.5 && open "$URL") &

cd "$SCRIPT_DIR"
# Usa python3.12 per compatibilità con claude-agent-sdk (richiede 3.10+)
exec python3.12 server.py --hub "$HUB" --port "$PORT"
