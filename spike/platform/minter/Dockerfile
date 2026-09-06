FROM python:3.12-alpine
RUN apk add --no-cache openssl && adduser -D -u 10001 minter
COPY minter.py /minter.py
USER 10001
ENTRYPOINT ["python3", "-u", "/minter.py"]
