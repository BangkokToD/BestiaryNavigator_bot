FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN groupadd --system bn \
    && useradd --system --gid bn --home-dir /app --shell /usr/sbin/nologin bn

COPY pyproject.toml ./pyproject.toml

RUN python -m pip install --upgrade pip \
    && python -c 'import tomllib; data = tomllib.load(open("pyproject.toml", "rb")); print("\n".join(data["project"]["dependencies"]))' > /tmp/requirements.txt \
    && python -m pip install -r /tmp/requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY materials ./materials

RUN python -m pip install --no-deps .

RUN chown -R bn:bn /app

USER bn

EXPOSE 8000

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
