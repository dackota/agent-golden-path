FROM python:3.12-slim
RUN useradd -u 10001 -m app
WORKDIR /app
COPY api.py mcp_server.py ./
USER 10001
# CMD is chosen per Deployment: ["python","-u","api.py"] or ["python","-u","mcp_server.py"]
CMD ["python", "-u", "mcp_server.py"]
