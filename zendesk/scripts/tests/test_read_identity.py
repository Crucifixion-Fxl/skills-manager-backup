"""Synthetic identity/projection and fixed-tenant transport regression tests."""
import importlib.util
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).parents[1] / "read_identity.py"
spec = importlib.util.spec_from_file_location("zendesk_identity", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class IdentityTests(unittest.TestCase):
    def body(self):
        return {"user": {"email": "fixture@example.invalid", "name": "Fixture",
                         "role": "agent", "token": "SYNTHETIC_SECRET", "id": 123}}

    def test_matching_identity_projects_only_verified_fields(self):
        value = module.decode(self.body(), "fixture@example.invalid", "Fixture")
        self.assertEqual(value, {"expectedEmailMatched": True, "expectedNameMatched": True, "role": "agent"})
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(value))

    def test_subject_mismatch_and_unknown_role_refused(self):
        for email, name in [("other@example.invalid", "Fixture"), ("fixture@example.invalid", "Other")]:
            with self.assertRaisesRegex(module.Rejected, "IDENTITY_MISMATCH"):
                module.decode(self.body(), email, name)
        value = self.body(); value["user"]["role"] = "unknown"
        with self.assertRaisesRegex(module.Rejected, "INVALID_IDENTITY"):
            module.decode(value, "fixture@example.invalid", "Fixture")

    def test_credential_newline_and_redirect_refused(self):
        with self.assertRaisesRegex(module.Rejected, "INVALID_CREDENTIAL"):
            module.read_identity("synthetic\nheader", "fixture@example.invalid", "Fixture")
        with self.assertRaisesRegex(module.Rejected, "REDIRECT_REJECTED"):
            module.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_fixed_read_target_and_response_limit(self):
        body = json.dumps(self.body()).encode()
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                self.limit = size
                return body
        response = Response()
        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return response
        opener = Opener()
        module.read_identity("SYNTHETIC_CREDENTIAL", "fixture@example.invalid", "Fixture", opener)
        self.assertEqual(opener.request.full_url, module.URL)
        self.assertEqual(opener.request.get_method(), "GET")
        self.assertEqual(opener.timeout, 20)
        self.assertEqual(response.limit, module.LIMIT + 1)
        body = b"x" * (module.LIMIT + 1)
        with self.assertRaisesRegex(module.Rejected, "BODY_LIMIT"):
            module.read_identity("SYNTHETIC_CREDENTIAL", "fixture@example.invalid", "Fixture", opener)

if __name__ == "__main__":
    unittest.main()
