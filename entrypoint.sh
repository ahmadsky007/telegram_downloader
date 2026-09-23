#!/bin/bash

# Start PO Token provider on 127.0.0.1:4416
echo "[entrypoint] Starting PO Token provider on 127.0.0.1:4416..."
if [ -f /usr/local/bin/bgutil-pot ]; then
    /usr/local/bin/bgutil-pot server --host 127.0.0.1 --port 4416 &
    POT_PID=$!
    sleep 1
    if kill -0 "$POT_PID" 2>/dev/null; then
        echo "[entrypoint] ✅ PO Token provider running on :4416 (PID=$POT_PID)"
    else
        echo "[entrypoint] ⚠️  PO Token provider failed to start"
    fi
fi

# Start the bot application
echo "[entrypoint] Starting bot application on :${PORT:-8080}..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
