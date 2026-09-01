FROM python:3.12-slim

# curl fetches supercronic below and runs the healthcheck ping in the crontab.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# supercronic instead of system cron: runs correctly as PID 1, logs to stdout,
# and needs no crontab-environment workarounds. Both architectures are pinned
# because the VPS is amd64 while local builds on Apple Silicon are arm64.
ARG SUPERCRONIC_VERSION=v0.2.48
ARG SUPERCRONIC_SHA1_AMD64=016b7c9aebfc8d9fd9526e8ba33b191fc524485f
ARG SUPERCRONIC_SHA1_ARM64=2ab9b3bdcf290f60b59700aad876b6e68f3a6b06
RUN set -eu; \
    case "$(dpkg --print-architecture)" in \
      amd64) arch=amd64; sha1="${SUPERCRONIC_SHA1_AMD64}" ;; \
      arm64) arch=arm64; sha1="${SUPERCRONIC_SHA1_ARM64}" ;; \
      *) echo "unsupported arch: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${arch}"; \
    echo "${sha1}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The booking window is local midnight 7 days before the lesson, and the cron
# trigger times are local too, so the container clock has to match the
# schedule's timezone.
ENV TZ=Europe/Madrid \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8008", "web:app"]
