#!/bin/bash
# ChitUI Runner with Auto-Restart
# This wrapper script runs ChitUI and automatically restarts it when requested

cd "$(dirname "$0")"

echo "=== ChitUI Runner Started ==="
echo "Log file: $(pwd)/chitui.log"
echo ""

# Function to check if port is in use
check_port() {
    if command -v netstat >/dev/null 2>&1; then
        netstat -tuln 2>/dev/null | grep -q ":8080 "
    elif command -v ss >/dev/null 2>&1; then
        ss -tuln 2>/dev/null | grep -q ":8080 "
    elif command -v lsof >/dev/null 2>&1; then
        lsof -i :8080 >/dev/null 2>&1
    else
        # If no tool available, just assume port is free after delay
        return 1
    fi
}

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting ChitUI..."

    # Run with Python Flask development server and capture exit code
    python3 main.py
    EXIT_CODE=$?

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ChitUI exited with code: $EXIT_CODE"

    # Exit code 42 means restart was requested
    if [ $EXIT_CODE -eq 42 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restart requested, waiting for port 8080 to be released..."

        # Wait for port to be released (max 10 seconds)
        for i in {1..20}; do
            if ! check_port; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] Port 8080 is free"
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
