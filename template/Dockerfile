# PROTOTYPE. Real path: FROM the platform hardened base image (Wolfi/distroless, pinned SDKs).
FROM python:3.12-slim
RUN useradd -u 10001 -m app
WORKDIR /app
COPY app.py .
USER 10001
ENV PORT=8080
CMD ["python", "-u", "app.py"]
