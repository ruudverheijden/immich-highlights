FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY albums.toml.example /app/albums.toml
COPY src /app/src
WORKDIR /app/src
ENV PYTHONPATH=/app/src
ENV SCORER_ALBUM_CONFIG_PATH=/app/albums.toml
CMD ["python", "scorer.py"]
