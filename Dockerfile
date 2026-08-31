FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY rtt_app ./rtt_app
RUN pip install --no-cache-dir .

EXPOSE 8765
CMD ["python", "-m", "rtt_app.action_api", "--host", "0.0.0.0"]
