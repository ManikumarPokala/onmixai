# OnMixAI backend image — pinned to Python 3.12 to match the engineering contract
# (CLAUDE.md §10). Multi-stage: build wheels once, run as a non-root user.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency layer — copy only the project metadata first for build-cache reuse.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Run as an unprivileged user; the process never needs root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "src.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
