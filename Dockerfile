FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Uvicorn with multiple workers would be set via $WEB_CONCURRENCY in prod;
# kept single-worker here since horizontal scaling in this design happens
# by running MORE CONTAINERS (see ARCHITECTURE.md), not more in-process workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
