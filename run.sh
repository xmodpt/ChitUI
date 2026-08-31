#!/bin/bash
# ChitUI Runner with Auto-Restart
# This wrapper script runs ChitUI and automatically restarts it when requested

cd "$(dirname "$0")"

echo "=== ChitUI Runner Started ==="
echo "Log file: $(pwd)/chitui.log"
echo ""

# ── Ownership guard ────────────────────────────────────────────────────────
# If the data/ folder (which holds chitui_settings.json and .secret_key) is
# owned by root (happens after an accidental 'sudo python3 main.py'), the
# regular user cannot read the settings and the password gets reset on every
# boot.  Fix it automatically before starting.
DATA_DIR="$(pwd)/data"
if [ -d "$DATA_DIR" ]; then
    OWNER=$(stat -c '%U' "$DATA_DIR")
    if [ "$OWNER" = "root" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: data/ is owned by root — fixing ownership..."
        if sudo chown -R "$(whoami)":"$(whoami)" "$DATA_DIR"; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ownership fixed to $(whoami)"
        else
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Could not fix ownership (no sudo?). Settings may not persist!"
        fi
    fi
fi
# ──────────────────────────────────────────────────────────────────────────

# Determine which port ChitUI is configured to listen on: the PORT env var
# takes priority, otherwise read Settings → Network ("network.port" in
# data/chitui_settings.json), falling back to the default of 8080.
get_configured_port() {
    if [ -n "$PORT" ]; then
        echo "$PORT"
        return
    fi
    python3 -c "
import json
try:
    with open('data/chitui_settings.json') as f:
        d = json.load(f)
    p = d.get('network', {}).get('port', 8080)
    print(p if isinstance(p, int) and 1 <= p <= 65535 else 8080)
except Exception:
    print(8080)
" 2>/dev/null || echo 8080
}

# Function to check if port is in use
check_port() {
    local p
    p=$(get_configured_port)
    if command -v netstat >/dev/null 2>&1; then
        netstat -tuln 2>/dev/null | grep -q ":${p} "
    elif command -v ss >/dev/null 2>&1; then
        ss -tuln 2>/dev/null | grep -q ":${p} "
    elif command -v lsof >/dev/null 2>&1; then
        lsof -i ":${p}" >/dev/null 2>&1
    else
        # If no tool available, just assume port is free after delay
        return 1
    fi
}

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting ChitUI..."

    # Run with Python Flask development server and capture exit code
    python3 main.py 2>&1 | tee -a chitui.log
    EXIT_CODE=${PIPESTATUS[0]}

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ChitUI exited with code: $EXIT_CODE"

    # Exit code 42 means restart was requested
    if [ $EXIT_CODE -eq 42 ]; then
        RUN_PORT=$(get_configured_port)
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restart requested, waiting for port ${RUN_PORT} to be released..."

        # Wait for port to be released (max 10 seconds)
        for i in {1..20}; do
            if ! check_port; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] Port ${RUN_PORT} is free"
                break
            fi
            sleep 0.5
        done

        # Extra delay for safety
        sleep 1
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restarting..."
        continue
    fi

    # Any other exit code means we should stop
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ChitUI stopped"
    break
done

echo "=== ChitUI Runner Stopped ==="
