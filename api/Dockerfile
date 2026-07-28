FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app


#python dependencies

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

#app code

COPY api/app ./app
COPY api/alembic ./alembic
COPY api/alembic.ini ./alembic.ini

#run from root

RUN useradd --create-home --shell /bin/bash apiuser && \
    chown -R apiuser:apiuser /app
USER apiuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]