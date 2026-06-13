FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (cached unless pyproject.toml changes)
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

# Bot source code
COPY bot/bot ./bot

# Non-root user
RUN useradd --create-home --shell /bin/bash botuser && \
    chown -R botuser:botuser /app
USER botuser

# No EXPOSE — long-polling means no inbound traffic
CMD ["python", "-m", "bot.main"]