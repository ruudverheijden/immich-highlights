FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY albums.toml.example /app/albums.toml
COPY content_filters.toml.example /app/content_filters.toml
COPY src /app/src
WORKDIR /app/src
ENV PYTHONPATH=/app/src
ENV SCORER_ALBUM_CONFIG_PATH=/app/albums.toml
ENV SCORER_CONTENT_FILTER_CONFIG_PATH=/app/content_filters.toml
CMD ["python", "scorer.py"]
