"""Traces for a stdlib-only agent. OTLP/HTTP JSON, no SDK.

One Trace per request. Span names follow the OpenTelemetry GenAI conventions:
`invoke_agent <agent>`, `chat <model>`, `execute_tool <tool>`. Token counts
and cost are recorded. Message content is not.

Reads the three variables the platform chart sets: OTEL_SERVICE_NAME,
OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_RESOURCE_ATTRIBUTES. No endpoint, no send.
Pass a span's traceparent() to the LLM gateway and the tool gateway so their
spans join the same trace.
"""
import json
import os
import secrets
import sys
import time
import urllib.request

INTERNAL, SERVER, CLIENT = 1, 2, 3
STATUS_OK, STATUS_ERROR = 1, 2
EXPORT_TIMEOUT_SECONDS = 5
SCOPE = "goldenpath.agent"


def attribute(key, value):
    """One OTLP attribute. int64 travels as a decimal string, per the OTLP JSON rules."""
    if isinstance(value, bool):
        typed = {"boolValue": value}
    elif isinstance(value, int):
        typed = {"intValue": str(value)}
    elif isinstance(value, float):
        typed = {"doubleValue": value}
    else:
        typed = {"stringValue": str(value)}
    return {"key": key, "value": typed}


def parse_resource(raw):
    """'a=1,b=2' to {'a': '1', 'b': '2'}. Pairs with no key or no '=' are skipped."""
    out = {}
    for pair in raw.split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def _post(url, body):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=EXPORT_TIMEOUT_SECONDS).read()


class Span:
    def __init__(self, trace, name, kind, parent_id=None):
        self.trace, self.name, self.kind, self.parent_id = trace, name, kind, parent_id
        self.span_id = secrets.token_hex(8)
        self.start, self.end = str(time.time_ns()), None
        self.attrs, self.status = {}, STATUS_OK

    def set(self, attrs):
        self.attrs.update({k: v for k, v in attrs.items() if v is not None})
        return self

    def error(self, exc):
        self.status = STATUS_ERROR
        self.attrs["error.type"] = type(exc).__name__
        return self

    def finish(self):
        self.end = str(time.time_ns())
        return self

    def child(self, name, kind=CLIENT):
        return self.trace.span(name, kind, parent_id=self.span_id)

    def traceparent(self):
        return f"00-{self.trace.trace_id}-{self.span_id}-01"

    def to_json(self):
        out = {"traceId": self.trace.trace_id, "spanId": self.span_id, "name": self.name,
               "kind": self.kind, "startTimeUnixNano": self.start, "endTimeUnixNano": self.end,
               "attributes": [attribute(k, v) for k, v in self.attrs.items()],
               "status": {"code": self.status}}
        if self.parent_id:
            out["parentSpanId"] = self.parent_id
        return out


class Trace:
    def __init__(self, service=None, resource=None, endpoint=None):
        env = os.environ.get
        self.service = service if service is not None else env("OTEL_SERVICE_NAME", "agent")
        self.resource = resource if resource is not None else env("OTEL_RESOURCE_ATTRIBUTES", "")
        self.endpoint = (endpoint if endpoint is not None else env("OTEL_EXPORTER_OTLP_ENDPOINT", "")).rstrip("/")
        self.trace_id = secrets.token_hex(16)
        self.spans = []

    def span(self, name, kind=INTERNAL, parent_id=None):
        span = Span(self, name, kind, parent_id)
        self.spans.append(span)
        return span

    def payload(self):
        resource = {"service.name": self.service, **parse_resource(self.resource)}
        return {"resourceSpans": [{
            "resource": {"attributes": [attribute(k, v) for k, v in resource.items()]},
            "scopeSpans": [{"scope": {"name": SCOPE},
                            "spans": [s.to_json() for s in self.spans if s.end]}]}]}

    def export(self, post=_post):
        """Send once. A failed export is printed, never raised. Telemetry must not break a reply."""
        if not self.endpoint:
            return False
        try:
            post(f"{self.endpoint}/v1/traces", json.dumps(self.payload()).encode())
            return True
        except (OSError, ValueError) as e:
            print(f"otel export failed: {e}", file=sys.stderr)
            return False
