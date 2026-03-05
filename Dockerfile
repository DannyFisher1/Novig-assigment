FROM python:3.13-slim

WORKDIR /app

# check health and install uv
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY core/ ./core/
COPY replica/ ./replica/

# Default to core service
CMD ["uv", "run", "uvicorn", "core.app:app", "--host", "0.0.0.0", "--port", "8000"]
