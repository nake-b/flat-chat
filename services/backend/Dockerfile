FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev
COPY . .
RUN uv sync --no-dev
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "flat_chat.main:app", "--host", "0.0.0.0", "--port", "8000"]
