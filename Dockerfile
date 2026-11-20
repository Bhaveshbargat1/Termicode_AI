FROM python:3.11-slim

LABEL maintainer="TermiCode AI Team"
LABEL description="TermiCode AI - Safe, Bounded Terminal Coding Assistant"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging metadata and source code
COPY pyproject.toml requirements.txt README.md ./
COPY src/ ./src/

# Install dependencies and termicode package
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install -e .

# Create non-root developer user for safe execution
RUN useradd -m -s /bin/bash termicode && \
    chown -R termicode:termicode /workspace
USER termicode

CMD ["termicode", "chat"]
