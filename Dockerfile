FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dlt.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dlt.txt
COPY backend/ ./backend/
RUN useradd -m appuser && mkdir -p /app/backend/app/data \
    && chown -R appuser:appuser /app \
    && chmod +x /app/backend/entrypoint.sh
WORKDIR /app/backend
# Start as root only to fix ownership of a possibly root-owned data volume,
# then drop to appuser inside entrypoint.sh (the server runs unprivileged).
USER root
ENTRYPOINT ["/app/backend/entrypoint.sh"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]