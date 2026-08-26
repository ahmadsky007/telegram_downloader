#!/bin/bash
set -e

WARP_OK=0
POT_OK=0

# ── 1. Start Cloudflare WARP (SOCKS5 proxy mode) ──
echo "[entrypoint] Starting Cloudflare WARP..."
if command -v warp-svc &>/dev/null; then
    # D-Bus is required by warp-svc
    mkdir -p /run/dbus
    if command -v dbus-daemon &>/dev/null; then
        dbus-daemon --system --nofork &>/dev/null &
        sleep 1
    fi

    mkdir -p /var/lib/cloudflare-warp
    warp-svc &
    WARP_SVC_PID=$!
    sleep 3  # Wait for daemon to initialize

    # Register if not already registered
    if ! warp-cli --accept-tos registration show 2>/dev/null | grep -q "Account"; then
        warp-cli --accept-tos registration new 2>/dev/null || true
    fi

    # Set WARP to proxy mode (SOCKS5 on localhost, no TUN device needed)
    warp-cli --accept-tos mode proxy 2>/dev/null || true
    warp-cli --accept-tos proxy port 40000 2>/dev/null || true
    warp-cli --accept-tos connect 2>/dev/null || true

    # Wait for WARP to actually connect (up to 15 seconds)
    for i in $(seq 1 15); do
        if warp-cli --accept-tos status 2>/dev/null | grep -q "Connected"; then
            echo "[entrypoint] ✅ WARP connected — SOCKS5 proxy on 127.0.0.1:40000"
            WARP_OK=1
            break
        fi
        echo "[entrypoint] Waiting for WARP connection... ($i/15)"
        sleep 1
    done

    if [ "$WARP_OK" -eq 0 ]; then
        echo "[entrypoint] ⚠️  WARP did not connect in time — continuing without proxy"
    fi
else
    echo "[entrypoint] ⚠️  warp-svc not found — skipping WARP"
fi

# ── 2. Start PO Token provider (BotGuard attestation) ──
if [ -f /usr/local/bin/bgutil-pot ]; then
    echo "[entrypoint] Starting PO Token provider on :4416..."
    /usr/local/bin/bgutil-pot server --port 4416 &
    POT_PID=$!
    sleep 2
    # Quick health check
    if kill -0 "$POT_PID" 2>/dev/null; then
        echo "[entrypoint] ✅ PO Token provider started (PID=$POT_PID)"
        POT_OK=1
    else
        echo "[entrypoint] ⚠️  PO Token provider failed to start — continuing without it"
    fi
else
    echo "[entrypoint] ⚠️  bgutil-pot not found — skipping PO Token provider"
fi

# ── 3. Export PROXY env var if WARP is running ──
if [ "$WARP_OK" -eq 1 ]; then
    export PROXY="socks5://127.0.0.1:40000"
    echo "[entrypoint] PROXY=$PROXY"
fi

# ── 4. Summary ──
echo "[entrypoint] ─────────────────────────────────"
echo "[entrypoint]  WARP proxy:     $([ $WARP_OK -eq 1 ] && echo '✅ active' || echo '❌ inactive')"
echo "[entrypoint]  PO Token:       $([ $POT_OK -eq 1 ] && echo '✅ active' || echo '❌ inactive')"
echo "[entrypoint] ─────────────────────────────────"

# ── 5. Start the bot application ──
echo "[entrypoint] Starting bot application..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
