FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py auth.py db.py admin.html dashboard.html ./
COPY css ./css
COPY js ./js
COPY img ./img

# База и ключ сессий живут в томе /app/instance.
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /app/instance \
    && chown app:app /app/instance
USER app
VOLUME ["/app/instance"]

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=2)" || exit 1

# Один процесс с потоками: ограничение попыток входа хранится в памяти процесса.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", \
     "--access-logfile", "-", "app:create_app()"]
