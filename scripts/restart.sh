#!/bin/bash
# ChitUI Restart Helper Script
# This script is called by the application to restart itself

# Get the PID of the calling process (passed as first argument)
OLD_PID=$1

echo "[ChitUI Restart] Waiting for old process ($OLD_PID) to exit..."

# Wait for the old process to fully exit and release port 8080
for i in {1..30}; do
    if ! kill -0 $OLD_PID 2>/dev/null; then
        echo "[ChitUI Restart] Old process has exited"
        break
    fi
    sleep 1
done

# Extra delay to ensure port is released
echo "[ChitUI Restart] Waiting for port 8080 to be released..."
sleep 3

# Change to the script directory
cd "$(dirname "$0")"

echo "[ChitUI Restart] Starting new ChitUI instance..."

# Detect how to run Python (check if we're in a venv)
if [ -n "$VIRTUAL_ENV" ]; then
    # We're in a virtual environment
    PYTHON_CMD="$VIRTUAL_ENV/bin/python3"
else
    # Use system Python
    PYTHON_CMD="python3"
fi

# Check if original user was running with sudo
if [ -n "$SUDO_USER" ]; then
    echo "[ChitUI Restart] Restarting with sudo as original user was using sudo"
    # Running with sudo - need to preserve sudo for the new instance
    if [ -n "$PORT" ]; then
        sudo -E PORT=$PORT $PYTHON_CMD main.py >> chitui.log 2>&1 &
    else
        sudo -E $PYTHON_CMD main.py >> chitui.log 2>&1 &
    fi
else
    # Not using sudo
    if [ -n "$PORT" ]; then
        PORT=$PORT $PYTHON_CMD main.py >> chitui.log 2>&1 &
    else
        $PYTHON_CMD main.py >> chitui.log 2>&1 &
    fi
fi

NEW_PID=$!
echo "[ChitUI Restart] New ChitUI instance started (PID: $NEW_PID)"
echo "[ChitUI Restart] Logs: $(pwd)/chitui.log"
