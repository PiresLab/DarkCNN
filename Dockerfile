FROM node:22-slim AS ui
WORKDIR /ui
COPY src/darkcnn/web/ui/package*.json ./
RUN npm ci
COPY src/darkcnn/web/ui ./
RUN npx vite build --outDir /ui-dist --emptyOutDir

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=ui /ui-dist ./src/darkcnn/web/static
RUN pip install ".[web,tiktok]"

# Chromium do Playwright: o login do TikTok (QR code) e a assinatura dos uploads rodam nele.
# Imagem menor sem postagem no TikTok: docker build --build-arg INSTALL_BROWSER=false .
ARG INSTALL_BROWSER=true
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$INSTALL_BROWSER" = "true" ]; then python -m playwright install --with-deps chromium; fi

VOLUME /data
EXPOSE 8765
CMD ["darkcnn", "web", "--host", "0.0.0.0", "--no-browser", "--no-worker"]
