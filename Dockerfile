FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium + ALL system dependencies automatically
# --with-deps runs apt-get update, installs required libs, then installs Chromium
RUN python -m playwright install --with-deps chromium

# Copy application code
COPY . .

# Start server (FAST — no installation steps)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
