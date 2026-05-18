FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN groupadd --system bn \
    && useradd --system --gid bn --home-dir /app --shell /usr/sbin/nologin bn

COPY pyproject.toml ./pyproject.toml
COPY app ./app
COPY frontend ./frontend
COPY materials ./materials
COPY migrations ./migrations

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN chown -R bn:bn /app

USER bn

EXPOSE 8000

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
