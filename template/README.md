# Template agent app

This folder is the source of the public template repo
[agent-app-template](https://github.com/dackota/agent-app-template). A dev
clicks "Use this template" there. The copy here keeps the platform repo
self-contained.

What is in it:

| File | Purpose |
|---|---|
| `app.py` | A tiny agent. Stdlib only. Talks to the LLM gateway and the tool gateway using only the env vars the chart injects. |
| `otel.py` | Sends one trace per request to the platform collector. Stdlib only. Reads `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_RESOURCE_ATTRIBUTES`. Emits token counts and cost, never message content. |
| `Dockerfile` | Non-root, pinned base. Real path: the platform hardened base image. |
| `agent.yaml` | The same values the chart takes. CI checks the image tag equals `VERSION`. |
| `VERSION` | One line. Bump it with the image tag. |
| `.github/workflows/build.yml` | Calls the shared `build-agent` workflow in this repo. |

The app must answer `GET /healthz` with 200. Everything else is up to the dev.
