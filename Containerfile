# Run the worker without touching the host's Python (works with Docker or rootless Podman):
#   podman build -t ats-worker .
#   podman run -d --name ats-worker --network host \
#     -v ./.env:/app/.env:ro -v ./drive-token.json:/app/drive-token.json:ro ats-worker
# --network host lets it reach Ollama and the portal on the host's localhost.
FROM docker.io/library/python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ats_worker ats_worker
COPY tests tests
CMD ["python", "-m", "ats_worker", "run"]
