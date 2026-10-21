FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CHROMIUM_EXECUTABLE=/usr/bin/chromium
COPY requirements.txt .
RUN --mount=type=secret,id=cloud_ca,mode=0444 set -eu; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    ca_file=/etc/ssl/certs/ca-certificates.crt; \
    if [ -f /run/secrets/cloud_ca ]; then ca_file=/run/secrets/cloud_ca; fi; \
    apt-get -o Acquire::https::CaInfo="$ca_file" update -o APT::Update::Error-Mode=any; \
    apt-get -o Acquire::https::CaInfo="$ca_file" install -y --no-install-recommends chromium fonts-noto-cjk libnss3-tools xvfb xauth; \
    rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=cloud_ca,mode=0444 if [ -f /run/secrets/cloud_ca ]; then \
      PIP_CERT=/run/secrets/cloud_ca pip install --no-cache-dir -r requirements.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi
COPY . .
RUN useradd -m -u 10001 career && mkdir -p artifacts state && chown -R career:career /app
USER career
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
