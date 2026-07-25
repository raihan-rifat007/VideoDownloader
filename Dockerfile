FROM denoland/deno:bin-2.8.1@sha256:324e7887040b297299ec097be8dc570f5eca2c808fe3ad9c52c61f9ab588938c AS deno
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=deno /deno /usr/local/bin/deno

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 reclip && \
    mkdir -p /app/downloads /data && \
    chown -R reclip:reclip /app /data
USER reclip

# Put the reclip user's --user installs first so startup yt-dlp updates take effect.
ENV PATH=/home/reclip/.local/bin:$PATH

EXPOSE 8899

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["gunicorn", "-b", "0.0.0.0:8899", "-w", "1", "--threads", "4", "--timeout", "3600", "--access-logfile", "-", "app:app"]
