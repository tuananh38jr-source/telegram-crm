FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium system dependencies manually.
# python:3.11-slim = Debian Trixie. Playwright 1.44 --with-deps falls back to
# Ubuntu 20.04 names which don't exist (ttf-ubuntu-font-family, ttf-unifont).
# So we install deps ourselves with correct Debian package names, then install
# Chromium binary only (no --with-deps).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libatspi2.0-0 libx11-6 libxcomposite1 \
    libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 \
    libxkbcommon0 libpango-1.0-0 libcairo2 libasound2 \
    libxshmfence0 libdbus-1-3 libbrotli1 libglib2.0-0 \
    libxcb1 fonts-unifont fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Install ONLY the Chromium binary (system deps already handled above)
RUN python -m playwright install chromium

# Copy application code
COPY . .

# Start server (FAST — no installation steps)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
