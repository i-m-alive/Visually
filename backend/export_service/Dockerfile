FROM python:3.11-slim

# Install Node.js (LTS) + Chromium + Puppeteer system deps
RUN apt-get update && apt-get install -y \
    curl gnupg \
    nodejs npm \
    chromium \
    libglib2.0-0 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
    libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 \
    libxrender1 libxss1 libxtst6 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libgbm1 libasound2 \
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

# Point Puppeteer at the system Chromium (no bundled download needed)
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Install Node deps (puppeteer-core uses system Chromium)
COPY export_service/package.json ./package.json
RUN npm install --omit=dev

# Install Python deps
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY export_service/ ./export_service/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 3002

CMD ["uvicorn", "export_service.main:app", "--host", "0.0.0.0", "--port", "3002"]
