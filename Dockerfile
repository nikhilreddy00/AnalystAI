FROM python:3.11-slim

WORKDIR /app

# Install uv (pinned for reproducibility)
RUN pip install uv==0.5.21

# Copy dependency manifests first — layer is cached unless they change
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Copy application source (excluded by .dockerignore)
COPY . .

# Uploads directory must exist before the app starts
RUN mkdir -p uploads

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
