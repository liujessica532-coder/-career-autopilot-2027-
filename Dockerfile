FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CHROMIUM_EXECUTABLE=/usr/bin/chromium
COPY requirements.txt .
RUN --mount=type=secret,id=cloud_ca if [ -f /run/secrets/cloud_ca ]; then cp /run/secrets/cloud_ca /etc/ssl/certs/ca-certificates.crt; fi; sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get update -o APT::Update::Error-Mode=any && apt-get install -y --no-install-recommends chromium fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m -u 10001 career && mkdir -p artifacts state && chown -R career:career /app
USER career
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
