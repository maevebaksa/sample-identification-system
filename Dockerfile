FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    SIS_DATA_DIR=/data \
    SIS_HOST=0.0.0.0 \
    SIS_PORT=8420 \
    SIS_MOONRAKER_URL=""

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY run.py .
COPY sis ./sis

RUN useradd --system --uid 1000 sis && mkdir /data && chown sis /data
USER sis
VOLUME /data
EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8420/api/info', timeout=4)"

CMD ["python", "run.py"]
