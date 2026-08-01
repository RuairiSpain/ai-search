# Pattern Compiler MCP server — minimal, dependency-light container.
# Runs the deterministic recommender over Streamable HTTP for Copilot Studio /
# Foundry. No model or network dependency at runtime.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY catalogue/ ./catalogue/

RUN pip install --no-cache-dir .

# The catalogue ships with the image; point the server at it explicitly.
ENV PATCOMP_CATALOGUE=/app/catalogue
ENV PORT=8080
EXPOSE 8080

# Container platforms send SIGTERM; the server handles shutdown cleanly.
CMD ["python", "-m", "patcomp_mcp", "--http", "--host", "0.0.0.0", "--port", "8080"]
