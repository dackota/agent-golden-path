"""Tests for the stdlib OTLP emitter the container agents ship with.

The shape rules come from the OTLP JSON mapping: ids are hex strings, int64
values are decimal strings, enums are integers, keys are lowerCamelCase.
Run with: python3 -m unittest discover -s tests -v
"""
import filecmp
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "template"))
import otel  # noqa: E402


def one_span(trace):
    return trace.payload()["resourceSpans"][0]["scopeSpans"][0]["spans"]


class Attributes(unittest.TestCase):

    def test_a_string_is_a_string_value(self):
        self.assertEqual(otel.attribute("k", "v"), {"key": "k", "value": {"stringValue": "v"}})

    def test_an_int_travels_as_a_decimal_string(self):
        self.assertEqual(otel.attribute("k", 42)["value"], {"intValue": "42"})

    def test_a_float_is_a_double(self):
        self.assertEqual(otel.attribute("k", 0.5)["value"], {"doubleValue": 0.5})

    def test_a_bool_is_a_bool_and_not_an_int(self):
        self.assertEqual(otel.attribute("k", True)["value"], {"boolValue": True})


class ResourceAttributes(unittest.TestCase):

    def test_parses_the_chart_format(self):
        self.assertEqual(otel.parse_resource("team=demo,platform.kind=container"),
                         {"team": "demo", "platform.kind": "container"})

    def test_skips_pairs_without_an_equals_sign_or_key(self):
        self.assertEqual(otel.parse_resource("junk,=x,team=demo,"), {"team": "demo"})

    def test_empty_input_is_an_empty_map(self):
        self.assertEqual(otel.parse_resource(""), {})


class Spans(unittest.TestCase):

    def test_ids_are_hex_of_the_right_length(self):
        t = otel.Trace()
        s = t.span("invoke_agent x", otel.SERVER).finish()
        self.assertEqual(len(t.trace_id), 32)
        self.assertEqual(len(s.span_id), 16)
        int(t.trace_id, 16)
        int(s.span_id, 16)

    def test_traceparent_is_w3c_shaped(self):
        t = otel.Trace()
        s = t.span("root", otel.SERVER)
        version, trace_id, span_id, flags = s.traceparent().split("-")
        self.assertEqual((version, trace_id, span_id, flags), ("00", t.trace_id, s.span_id, "01"))

    def test_a_child_carries_its_parent_id(self):
        t = otel.Trace()
        root = t.span("root", otel.SERVER)
        child = root.child("chat m", otel.CLIENT).finish()
        root.finish()
        by_name = {s["name"]: s for s in one_span(t)}
        self.assertEqual(by_name["chat m"]["parentSpanId"], root.span_id)
        self.assertNotIn("parentSpanId", by_name["root"])

    def test_times_are_nanosecond_strings_and_kind_is_an_integer(self):
        t = otel.Trace()
        t.span("root", otel.SERVER).finish()
        s = one_span(t)[0]
        self.assertIsInstance(s["kind"], int)
        self.assertGreaterEqual(int(s["endTimeUnixNano"]), int(s["startTimeUnixNano"]))
        self.assertGreater(int(s["startTimeUnixNano"]), 10**18)

    def test_none_attributes_are_dropped(self):
        t = otel.Trace()
        t.span("root", otel.SERVER).set({"a": 1, "b": None}).finish()
        keys = [a["key"] for a in one_span(t)[0]["attributes"]]
        self.assertEqual(keys, ["a"])

    def test_error_marks_the_status_and_records_the_type(self):
        t = otel.Trace()
        t.span("root", otel.SERVER).error(RuntimeError("boom")).finish()
        s = one_span(t)[0]
        self.assertEqual(s["status"]["code"], otel.STATUS_ERROR)
        self.assertIn({"key": "error.type", "value": {"stringValue": "RuntimeError"}}, s["attributes"])

    def test_unfinished_spans_are_not_exported(self):
        t = otel.Trace()
        t.span("open", otel.SERVER)
        t.span("done", otel.SERVER).finish()
        self.assertEqual([s["name"] for s in one_span(t)], ["done"])


class Payload(unittest.TestCase):

    def test_resource_names_the_service_and_the_chart_attributes(self):
        t = otel.Trace(service="vibe-app", resource="team=demo,platform.kind=container")
        attrs = t.payload()["resourceSpans"][0]["resource"]["attributes"]
        self.assertIn({"key": "service.name", "value": {"stringValue": "vibe-app"}}, attrs)
        self.assertIn({"key": "team", "value": {"stringValue": "demo"}}, attrs)


class Export(unittest.TestCase):

    def test_no_endpoint_sends_nothing(self):
        sent = []
        t = otel.Trace(endpoint="")
        t.span("root", otel.SERVER).finish()
        self.assertFalse(t.export(post=lambda url, body: sent.append(url)))
        self.assertEqual(sent, [])

    def test_posts_json_to_v1_traces(self):
        sent = []
        t = otel.Trace(endpoint="http://otel-collector:4318/")
        t.span("root", otel.SERVER).finish()
        self.assertTrue(t.export(post=lambda url, body: sent.append((url, body))))
        self.assertEqual(sent[0][0], "http://otel-collector:4318/v1/traces")
        self.assertIn(b'"resourceSpans"', sent[0][1])

    def test_a_failed_export_is_reported_and_never_raised(self):
        """Telemetry must never break a reply."""
        def boom(url, body):
            raise OSError("connection refused")
        t = otel.Trace(endpoint="http://otel-collector:4318")
        t.span("root", otel.SERVER).finish()
        self.assertFalse(t.export(post=boom))


class TheAuditorShipsTheSameModule(unittest.TestCase):
    """The template is mirrored to another repo, so the module is copied, not imported."""

    def test_copies_are_identical(self):
        self.assertTrue(filecmp.cmp(os.path.join(ROOT, "template", "otel.py"),
                                    os.path.join(ROOT, "apps", "renovate-auditor", "otel.py"),
                                    shallow=False))


if __name__ == "__main__":
    unittest.main()
