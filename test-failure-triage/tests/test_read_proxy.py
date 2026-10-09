import datetime as dt
import sys
from pathlib import Path
import unittest
import threading
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import rp_read_proxy as R


class ProxyTests(unittest.TestCase):
    def test_only_fixed_project_and_bounded_get_routes(self):
        self.assertTrue(R.allowed_path("/api/v1/builder_prod_cn/log?filter.eq.item=123&page.page=1&page.size=100"))
        self.assertTrue(R.allowed_path("/api/v1/data/builder_prod_cn/file-123.png"))
        for path in ["https://evil.test/api/v1/builder_prod_cn/log/1", "/api/users/5/api-keys",
                     "/api/v1/customer-care/log/1", "/api/v1/builder_prod_cn/log",
                     "/api/v1/builder_prod_cn/log?filter.eq.item=1&page.size=999",
                     "/api/v1/data/builder_prod_cn/../secret", "/api/v1/data/builder_prod_cn/%2e%2e",
                     "/api/v1/builder_prod_cn/item?filter.eq.launchId=1&token=secret",
                     "/api/v1/builder_prod_cn/log?filter.eq.item=1&filter.eq.item=2"]:
            self.assertFalse(R.allowed_path(path), path)

    def test_expiry_and_authentication_fail_closed(self):
        config = {"expires_at": "2026-11-04T00:00:00Z", "client_token": "private"}
        self.assertTrue(R.authorized(config, "Bearer private", dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)))
        self.assertFalse(R.authorized(config, "Bearer wrong"))
        self.assertFalse(R.authorized(config, "Bearer private", dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc)))

    def test_http_write_and_foreign_project_never_reach_upstream(self):
        server = R.ThreadingHTTPServer(("127.0.0.1", 0), R.handler(
            {"client_token": "test", "rp_token": "must-not-be-used", "expires_at": "2100-01-01T00:00:00Z"}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for method in ["POST", "PUT", "PATCH", "DELETE"]:
                request = urllib.request.Request(base + "/api/v1/builder_prod_cn/log/1",
                    method=method, headers={"Authorization": "Bearer test"})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=1)
                self.assertEqual(405, error.exception.code)
            for path, auth, status in [("/api/v1/customer-care/log/1", "Bearer test", 403),
                                       ("/api/v1/builder_prod_cn/log/1", "Bearer wrong", 401)]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(base+path, headers={"Authorization": auth}), timeout=1)
                self.assertEqual(status, error.exception.code)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
