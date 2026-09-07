"""Checks on the platform manifests that a person would otherwise have to remember.

The point of a gateway-held credential is that nothing else holds one and
nothing routes around it. Both are easy to undo by accident, so they are tested.
Run with: python3 -m unittest discover -s tests
"""
import glob
import os
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
GATEWAY_HOST = "agentgateway-proxy.agentgateway-system.svc.cluster.local"


def docs(path):
    with open(os.path.join(ROOT, path)) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def one(path, kind, name):
    for d in docs(path):
        if d.get("kind") == kind and d["metadata"]["name"] == name:
            return d
    raise AssertionError(f"no {kind}/{name} in {path}")


class OrdersReachesTheApiThroughTheGateway(unittest.TestCase):

    def test_the_mcp_wrapper_calls_the_gateway_not_the_api(self):
        """If this points straight at the API, the credential is bypassed."""
        mcp = one("platform/tools/orders.yaml", "MCPServer", "orders-mcp")
        url = mcp["spec"]["deployment"]["env"]["ORDERS_API_URL"]
        self.assertIn(GATEWAY_HOST, url)
        self.assertNotIn("orders-api.demo-apis", url)

    def test_the_wrapper_holds_no_credential_of_its_own(self):
        mcp = one("platform/tools/orders.yaml", "MCPServer", "orders-mcp")
        env = mcp["spec"]["deployment"]["env"]
        for name in env:
            self.assertNotIn("KEY", name.upper(), f"{name} looks like a credential")
            self.assertNotIn("TOKEN", name.upper(), f"{name} looks like a credential")

    def test_the_gateway_backend_injects_a_credential_from_a_secret(self):
        b = one("platform/gateway/orders-api.yaml", "AgentgatewayBackend", "orders-api")
        auth = b["spec"]["policies"]["auth"]
        self.assertIn("secretRef", auth)
        self.assertEqual(auth["location"]["header"]["name"], "X-API-Key")

    def test_the_gateway_backend_holds_no_inline_key(self):
        """auth.key is an inline literal. It must never appear in Git."""
        b = one("platform/gateway/orders-api.yaml", "AgentgatewayBackend", "orders-api")
        self.assertNotIn("key", b["spec"]["policies"]["auth"])


class NoSecretValuesInGit(unittest.TestCase):

    # The one Secret whose values are committed. They are CHANGE-ME placeholders
    # for a laptop cluster, and the platform guide says to replace them. Listing
    # it here means a second committed Secret fails this test and gets noticed.
    ALLOWED = {"litellm-secrets"}

    def test_no_new_platform_manifest_carries_secret_data(self):
        for path in glob.glob(os.path.join(ROOT, "platform", "**", "*.yaml"), recursive=True):
            with open(path) as f:
                for d in yaml.safe_load_all(f):
                    if not d or d.get("kind") != "Secret":
                        continue
                    name = d["metadata"]["name"]
                    if name in self.ALLOWED or not (d.get("data") or d.get("stringData")):
                        continue
                    self.fail(f"{os.path.relpath(path, ROOT)} commits a value for Secret {name}")

    def test_the_orders_credential_is_never_committed(self):
        for path in glob.glob(os.path.join(ROOT, "platform", "**", "*.yaml"), recursive=True):
            with open(path) as f:
                for d in yaml.safe_load_all(f):
                    if d and d.get("kind") == "Secret":
                        self.assertNotEqual(d["metadata"]["name"], "orders-api-credentials")


if __name__ == "__main__":
    unittest.main()
