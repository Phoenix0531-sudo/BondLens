FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_RUN_HOST=0.0.0.0
ENV PORT=5000

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

RUN groupadd --system bondlens && useradd --system --gid bondlens --home-dir /app bondlens
COPY --chown=bondlens:bondlens . .
RUN mkdir -p /app/.tmp && chown bondlens:bondlens /app/.tmp
USER bondlens

EXPOSE 5000

# One process keeps result URLs consistent; threads allow health checks during analysis.
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-", "-b", "0.0.0.0:5000", "app:app"]
