FROM python:3.11-slim

WORKDIR /app

# SQL Server sources (5.8b): pyodbc needs Microsoft's ODBC Driver 18 (Microsoft's EULA, accepted
# here; ~20 MB). The rest of the app runs without it.
# Microsoft's own repo package carries the repo and its current signing keys.
RUN apt-get update -q && apt-get install -yq --no-install-recommends curl ca-certificates \
    && . /etc/os-release \
    && curl -fsSL -o /tmp/ms.deb "https://packages.microsoft.com/config/debian/${VERSION_ID}/packages-microsoft-prod.deb" \
    && dpkg -i /tmp/ms.deb && rm /tmp/ms.deb \
    && apt-get update -q && ACCEPT_EULA=Y apt-get install -yq --no-install-recommends msodbcsql18 unixodbc \
    && apt-get purge -yq curl && apt-get autoremove -yq && rm -rf /var/lib/apt/lists/*

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
