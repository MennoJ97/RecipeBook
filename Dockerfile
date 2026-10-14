FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py importer.py processing.py backfill.py backfill_servings.py ./
COPY static ./static
# Run as an unprivileged user. A bind-mounted data directory must be writable by
# this uid on the host: chown -R 1000:1000 <data dir>
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app:app /data
USER app
ENV RECIPEBOOK_DATA=/data RECIPEBOOK_HOST=0.0.0.0 RECIPEBOOK_PORT=8080 PYTHONDONTWRITEBYTECODE=1
VOLUME /data
EXPOSE 8080
# HEAD / needs no password, so this works with or without RECIPEBOOK_PASSWORD.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8080/', method='HEAD'), timeout=4)"
CMD ["python", "server.py"]
