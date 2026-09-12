# Python 3.12 (not 3.14) because torch / sentence-transformers wheels are reliable here.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ml.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt
# Heavy ML deps in their own layer (CPU-only torch keeps the image ~1GB smaller).
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-ml.txt

COPY . .

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
