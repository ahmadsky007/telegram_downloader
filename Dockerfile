FROM python:3.12-slim

# ── Install system dependencies + Cloudflare WARP repo ──
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gnupg curl ca-certificates lsb-release dbus \
    && curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
       | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] \
       https://pkg.cloudflareclient.com/ bookworm main" \
       > /etc/apt/sources.list.d/cloudflare-warp.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg nodejs cloudflare-warp \
    && rm -rf /var/lib/apt/lists/*

# ── Download bgutil PO-Token provider (Rust binary, ~15 MB) ──
RUN curl -fsSL \
    https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-linux-x86_64 \
    -o /usr/local/bin/bgutil-pot \
    && chmod +x /usr/local/bin/bgutil-pot

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PORT=8080
CMD ["./entrypoint.sh"]
