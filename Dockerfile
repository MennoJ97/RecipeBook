FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py importer.py processing.py ./
COPY static ./static
ENV RECIPEBOOK_DATA=/data RECIPEBOOK_HOST=0.0.0.0 RECIPEBOOK_PORT=8080
VOLUME /data
EXPOSE 8080
CMD ["python", "server.py"]
