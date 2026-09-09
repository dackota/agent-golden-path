FROM python:3.12-slim
RUN useradd -u 10001 -m app
WORKDIR /app
COPY mcp_server.py ./
USER 10001
CMD ["python", "-u", "mcp_server.py"]
