from __future__ import annotations

import unittest
from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

from audience_sync import AudienceSyncClient, AudienceSyncConfig, SafeApiError
from audience_sync.project_operations import PersonalKeyOperation


class RedirectTests(unittest.TestCase):
    def test_client_rejects_redirects_without_following_or_retrying(self) -> None:
        source = "https://audience.example.test/api/platform/v3/personal-key"
        destination = "https://redirect.example.test/untrusted"
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                client = AudienceSyncClient(AudienceSyncConfig(
                    base_url="https://audience.example.test",
                    sync_api_key="awpk_v2_" + "a" * 26 + "_" + "A" * 43,
                    contract_revision="audience-project-v3",
                ))
                requested_urls = []

                def respond(_handler, request):
                    requested_urls.append(request.full_url)
                    self.assertEqual(request.full_url, source)
                    headers = Message()
                    headers["Location"] = destination
                    response = addinfourl(BytesIO(b""), headers, source, status)
                    response.msg = "Redirect"
                    return response

                # Keep the client's installed opener and redirect handler; only
                # replace its HTTPS transport so every attempt stays offline.
                with patch.object(HTTPSHandler, "https_open", autospec=True, side_effect=respond):
                    with self.assertRaises(SafeApiError) as raised:
                        client.call(PersonalKeyOperation.GET_PROJECT_PERSONAL_KEY_CONTEXT)
                expected_codes = {"redirect_rejected"}
                if status == 308:
                    # Older urllib rejects 308 as an HTTP error before redirect
                    # dispatch. Both paths must fail without following it.
                    expected_codes.add("audience_sync_request_failed")
                self.assertIn(raised.exception.code, expected_codes)
                self.assertEqual(str(raised.exception), raised.exception.code)
                self.assertEqual(requested_urls, [source])


if __name__ == "__main__":
    unittest.main()
