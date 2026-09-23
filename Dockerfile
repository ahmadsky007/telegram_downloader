FROM python:3.12-slim

# 1. System tools: ffmpeg, nodejs, curl, unzip, ca-certificates
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg nodejs curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 2. Download bgutil PO-Token provider binary (v0.8.1)
RUN curl -fsSL \
    https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/download/v0.8.1/bgutil-pot-linux-x86_64 \
    -o /usr/local/bin/bgutil-pot \
    && chmod +x /usr/local/bin/bgutil-pot

# 3. Install Deno for official yt-dlp JS challenge solving
RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm /tmp/deno.zip

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Install matching bgutil PO-Token yt-dlp plugin (v0.8.1) directly into app and global plugin dirs
RUN curl -fsSL \
    https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/download/v0.8.1/bgutil-ytdlp-pot-provider-rs.zip \
    -o /tmp/pot-plugin.zip \
    && unzip /tmp/pot-plugin.zip -d /tmp/pot-plugin \
    && cp -r /tmp/pot-plugin/yt_dlp_plugins ./yt_dlp_plugins \
    && SITE_PKG=$(python -c "import site; print(site.getsitepackages()[0])") \
    && cp -r /tmp/pot-plugin/yt_dlp_plugins "${SITE_PKG}/" \
    && rm -rf /tmp/pot-plugin /tmp/pot-plugin.zip

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PORT=8080
CMD ["./entrypoint.sh"]
