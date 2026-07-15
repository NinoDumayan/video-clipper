FROM python:3.13-slim

RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /app/venv/bin/pip install --no-cache-dir -r backend/requirements.txt

RUN /app/venv/bin/python3 -c "from faster_whisper import WhisperModel; WhisperModel('base')"

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json postcss.config.mjs next.config.ts ./
COPY app/ app/
COPY public/ public/
RUN npm run build

COPY backend/ backend/
COPY start.sh ./
RUN chmod +x start.sh

ENV WHISPER_MODEL_SIZE=base \
    APP_PASSWORD=admin \
    PORT=7860

EXPOSE 7860

CMD ["./start.sh"]
