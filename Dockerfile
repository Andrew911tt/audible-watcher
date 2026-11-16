# Headless server image (NAS / home server). Data lives in /data.
#   docker build -t audible-watcher .
#   docker run -d -p 5055:5055 -v audible-watcher-data:/data audible-watcher
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY templates templates
COPY static static
ENV AW_DATA_DIR=/data AW_HOST=0.0.0.0 AW_PORT=5055 TZ=UTC
# run as an unprivileged user that owns only the data folder
RUN useradd --system --uid 1000 --home-dir /data aw \
    && mkdir -p /data && chown aw:aw /data
USER aw
VOLUME /data
EXPOSE 5055
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5055/healthz', timeout=8)"
CMD ["python", "app.py"]
