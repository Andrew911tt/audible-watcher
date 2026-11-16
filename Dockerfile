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
VOLUME /data
EXPOSE 5055
CMD ["python", "app.py"]
