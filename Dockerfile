FROM python:3.13-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S -G app app && mkdir -p /data && chown app:app /data
COPY app/hub.py /app/hub.py
USER app
ENV DATA_DIR=/data PORT=8080 BIND_HOST=0.0.0.0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python3 -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/health',timeout=3).read()"
CMD ["python3", "/app/hub.py"]
