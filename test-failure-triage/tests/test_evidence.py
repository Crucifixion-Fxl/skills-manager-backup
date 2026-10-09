import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("triage_media", SCRIPTS / "fetch_evidence.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
ATTACHMENT = {"logId": "8", "contentPath": "/api/test/jobs/4/evidence/logs/8/content"}


class Response(io.BytesIO):
    headers = {"Content-Type": "image/png"}


class Opener:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def open(self, request, timeout):
        self.calls.append(request)
        return Response(self.payload)


class EvidenceTests(unittest.TestCase):
    def test_download_preserves_bytes_private_and_uninspected(self):
        opener = Opener(b"image bytes")
        with tempfile.TemporaryDirectory() as tmp:
            result = M.download("https://platform.example/graphql", "fixture-token", "4", ATTACHMENT,
                                Path(tmp) / "media", opener=opener)
            path = Path(result["path"])
            self.assertEqual(b"image bytes", path.read_bytes())
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual("not_inspected", result["inspection"])
            self.assertEqual("https://platform.example/api/test/jobs/4/evidence/logs/8/content", opener.calls[0].full_url)

    def test_foreign_cross_job_and_traversal_paths_refused_before_network(self):
        opener = Opener(b"anything")
        with tempfile.TemporaryDirectory() as tmp:
            for path in ["https://foreign.example/content", "/api/test/jobs/5/evidence/logs/8/content",
                         "/api/test/jobs/4/evidence/logs/9/content", "/api/test/jobs/4/evidence/logs/../content"]:
                with self.assertRaises(M.CollectionError):
                    M.download("https://platform.example/graphql", "fixture-token", "4",
                               dict(ATTACHMENT, contentPath=path), Path(tmp), opener=opener)
            self.assertEqual([], opener.calls)

    def test_oversize_is_missing_evidence_without_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(M.CollectionError):
                M.download("https://platform.example/graphql", "fixture-token", "4", ATTACHMENT,
                           Path(tmp), max_bytes=3, opener=Opener(b"1234"))
            self.assertEqual([], list(Path(tmp).iterdir()))

    def test_files_deduplicated_and_budget_not_reported_complete(self):
        bundle = {"incidents": [{"job": {"id": "4"}, "evidence": {"state": "AVAILABLE", "attachments": [ATTACHMENT,
                     ATTACHMENT, {"logId": "9", "contentPath": "/api/test/jobs/4/evidence/logs/9/content"}]}}]}
        def fake(*args, **kwargs):
            return {"evidence_id": "attachment:4/8"}
        result = M.fetch(bundle, "https://platform.example", "fixture-token", Path("unused"),
                         max_files=1, downloader=fake)
        self.assertEqual(1, len(result["files"]))
        self.assertFalse(result["complete"])
        self.assertEqual("file budget exhausted", result["missing_evidence"][0]["reason"])

    def test_missing_upstream_evidence_is_not_complete_empty_download(self):
        result = M.fetch({"incidents": [{"job": {"id": "4"}, "evidence": None}]},
                         "https://platform.example", "fixture-token", Path("unused"))
        self.assertFalse(result["complete"])
        self.assertEqual([], result["files"])
        self.assertTrue(result["missing_evidence"])
