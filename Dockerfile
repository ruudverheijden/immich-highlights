FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY src /app/src
WORKDIR /app/src
ENV PYTHONPATH=/app/src
CMD ["python", "scorer.py"]
