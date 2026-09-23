"""Offline tests for the paprika skill scripts: no network, no Paprika account, no charges.

Run: python3 -m unittest discover -s skills/paprika/tests -p 'test_*.py' -v
"""
import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location("paprika", SCRIPTS / "paprika.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def call(fn, *args, **kwargs):
    """Run fn and return (exit_code, stderr, stdout)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            fn(*args, **kwargs)
            code = 0
        except SystemExit as ex:
            code = ex.code
    return code, err.getvalue(), out.getvalue()


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


NO_NETWORK = mock.patch.object(P, "http", side_effect=AssertionError("network must not be used"))


class CredentialTests(unittest.TestCase):
    def test_only_environment_variables_are_read(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env") as f:
            f.write("PAPRIKA_API_KEY=from_file\n")
            f.flush()
            with mock.patch.dict(os.environ, {"PAPRIKA_ENV": f.name}, clear=True):
                self.assertEqual(P.env(), {})
            with mock.patch.dict(os.environ, {"PAPRIKA_API_KEY": "from_env"}, clear=True):
                self.assertEqual(P.env(), {"PAPRIKA_API_KEY": "from_env"})

    def test_missing_variable_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            code, err, _ = call(P.need, "PAPRIKA_PROJECT_ID")
        self.assertEqual(code, 1)
        self.assertIn("PAPRIKA_PROJECT_ID", err)

    def test_token_cache_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "paprika")
            with mock.patch.object(P, "CACHE_DIR", cache), mock.patch.object(P, "TOKEN_FILE", os.path.join(cache, "token")):
                P.save_token("secret-token")
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(cache, "token")).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(cache).st_mode), 0o700)

    def cache(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cache = os.path.join(d.name, "paprika")
        p1 = mock.patch.object(P, "CACHE_DIR", cache)
        p2 = mock.patch.object(P, "TOKEN_FILE", os.path.join(cache, "token"))
        p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        return d.name, cache, os.path.join(cache, "token")

    def test_loose_existing_token_file_and_cache_dir_are_tightened(self):
        _, cache, token = self.cache()
        os.mkdir(cache, 0o755)
        Path(token).write_text("old")
        os.chmod(token, 0o644)
        P.save_token("new")
        self.assertEqual(Path(token).read_text(), "new")
        self.assertEqual(stat.S_IMODE(os.stat(token).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(cache).st_mode), 0o700)

    def test_symlinked_token_file_is_replaced_not_followed(self):
        root, cache, token = self.cache()
        os.mkdir(cache, 0o700)
        victim = Path(root) / "precious.txt"
        victim.write_text("precious")
        os.symlink(victim, token)
        P.save_token("t")
        self.assertEqual(victim.read_text(), "precious")  # target neither truncated nor overwritten
        self.assertFalse(os.path.islink(token))
        self.assertEqual(Path(token).read_text(), "t")

    def test_symlinked_cache_dir_is_refused(self):
        root, cache, _ = self.cache()
        elsewhere = Path(root) / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, cache)
        code, err, _ = call(P.save_token, "t")
        self.assertEqual(code, 1)
        self.assertIn("refusing", err)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_read_token_refuses_symlink_loose_and_empty_files(self):
        root, cache, token = self.cache()
        os.mkdir(cache, 0o700)
        Path(token).write_text("tok")
        os.chmod(token, 0o600)
        self.assertEqual(P.read_token(), "tok")
        os.chmod(token, 0o644)
        self.assertIsNone(P.read_token())
        os.chmod(token, 0o600)
        Path(token).write_text("")
        self.assertIsNone(P.read_token())
        os.unlink(token)
        real = Path(root) / "real"
        real.write_text("tok")
        os.chmod(real, 0o600)
        os.symlink(real, token)
        self.assertIsNone(P.read_token())

    def test_console_logs_in_again_when_the_cached_token_is_loose(self):
        _, cache, token = self.cache()
        os.mkdir(cache, 0o700)
        Path(token).write_text("stale")
        os.chmod(token, 0o644)
        with mock.patch.object(P, "login", return_value="fresh") as login, \
                mock.patch.object(P, "http", return_value=(200, {})) as http:
            P.console("GET", "/v1/console/x")
        login.assert_called_once()
        self.assertEqual(http.call_args.kwargs["auth"], "Bearer fresh")


class UploadInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def upload(self, spec):
        with mock.patch.dict(os.environ, {"PAPRIKA_PROJECT_ID": "prj_x"}), \
                mock.patch.object(P, "console", side_effect=AssertionError("network must not be used")):
            return call(P.cmd_upload, NS(items=[spec]))

    def test_bad_arguments_fail_before_any_network_call(self):
        wav = self.dir / "a.wav"
        wav.write_bytes(b"x")
        for spec in ["nofile", "BAD_ROLE=" + str(wav), "REFERENCE_IMAGE=" + str(wav),
                     "REFERENCE_AUDIO=" + str(self.dir / "missing.wav"), "REFERENCE_AUDIO=" + str(self.dir / "a.txt")]:
            code, err, _ = self.upload(spec)
            self.assertEqual(code, 1, spec)
            self.assertIn("error:", err)

    def test_symlink_fifo_empty_and_oversized_files_are_refused(self):
        real = self.dir / "real.wav"
        real.write_bytes(b"x")
        link = self.dir / "link.wav"
        link.symlink_to(real)
        fifo = self.dir / "fifo.wav"
        os.mkfifo(fifo)
        empty = self.dir / "empty.wav"
        empty.write_bytes(b"")
        big = self.dir / "big.wav"
        with open(big, "wb") as f:
            f.truncate(P.MAX_BYTES["AUDIO"] + 1)  # sparse: no real disk use
        for path in (link, fifo, empty, big):
            code, err, _ = self.upload("REFERENCE_AUDIO=" + str(path))
            self.assertEqual(code, 1, path.name)
            self.assertIn("error:", err)

    def test_regular_file_within_limit_is_accepted(self):
        ok = self.dir / "ok.wav"
        ok.write_bytes(b"12345")
        self.assertEqual(P.check_file(str(ok), "AUDIO"), 5)


class UploadTargetTests(unittest.TestCase):
    def test_only_https_on_allowed_hosts_with_put_or_post(self):
        good = ["https://bucket.oss-cn-hangzhou.aliyuncs.com/k?sig=1", "https://up.paprika.art/x"]
        bad = ["http://bucket.aliyuncs.com/k", "https://evil.example/k", "https://evilaliyuncs.com/k",
               "https://x.aliyuncs.com.evil.example/k", "file:///etc/passwd", "ftp://a.aliyuncs.com/k", "", "https://"]
        for url in good:
            self.assertEqual(P.upload_target({"uploadUrl": url}), (url, "PUT"), url)
        for url in bad:
            self.assertIsNone(P.upload_target({"uploadUrl": url}), url)
        self.assertIsNone(P.upload_target({"uploadUrl": good[0], "uploadMethod": "DELETE"}))
        self.assertEqual(P.upload_target({"uploadUrl": good[0], "uploadMethod": "POST"})[1], "POST")

    def test_redirects_are_refused(self):
        req = urllib.request.Request("https://a.aliyuncs.com/k", method="PUT")
        self.assertIsNone(P._NoRedirect().redirect_request(req, None, 302, "Found", {}, "http://evil.example/"))


class PutFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile("w", delete=False)
        tmp.write("token")
        tmp.close()
        self.token = tmp.name
        self.addCleanup(os.unlink, tmp.name)
        patcher = mock.patch.object(P, "TOKEN_FILE", self.token)
        patcher.start()
        self.addCleanup(patcher.stop)

    def put(self, d):
        with mock.patch.object(P.urllib.request, "build_opener") as opener, \
                mock.patch.object(P, "http", return_value=(200, {})) as http:
            call(P.put_file, d, b"bytes", "audio/wav", "prj_x")
        return opener, http

    def test_rejected_target_never_receives_the_bytes(self):
        opener, http = self.put({"assetId": "a1", "uploadUrl": "https://evil.example/k"})
        opener.assert_not_called()
        self.assertEqual(http.call_args.args[:2], ("PUT", "/v1/console/uploads/a1/content?projectId=prj_x"))
        self.assertEqual(http.call_args.kwargs["raw"], b"bytes")

    def test_allowed_target_is_used_directly(self):
        opener, http = self.put({"assetId": "a1", "uploadUrl": "https://b.aliyuncs.com/k"})
        opener.return_value.open.assert_called_once()
        http.assert_not_called()

    def test_failed_direct_upload_or_redirect_falls_back_to_the_api(self):
        err = urllib.error.HTTPError("https://b.aliyuncs.com/k", 302, "Found", {}, None)
        with mock.patch.object(P.urllib.request, "build_opener") as opener, \
                mock.patch.object(P, "http", return_value=(200, {})) as http:
            opener.return_value.open.side_effect = err
            code, _, _ = call(P.put_file, {"assetId": "a1", "uploadUrl": "https://b.aliyuncs.com/k"}, b"b", "audio/wav", "p")
        self.assertEqual(code, 0)
        http.assert_called_once()

    def test_fallback_failure_is_an_error(self):
        with mock.patch.object(P, "http", return_value=(500, {})):
            code, err, _ = call(P.put_file, {"assetId": "a1", "uploadUrl": "http://x"}, b"b", "audio/wav", "p")
        self.assertEqual(code, 1)
        self.assertIn("upload failed", err)


def video_result(url, status="SUCCEEDED"):
    out = [{"kind": "VIDEO", "url": url, "width": 1, "height": 1, "durationMs": 1}] if url else []
    return 200, {"data": {"status": status, "progress": 100, "result": {"outputs": out}}}


class FinishTests(unittest.TestCase):
    def finish(self, response):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"PAPRIKA_API_KEY": "k"}), \
                mock.patch.object(P, "http", return_value=response), \
                mock.patch.object(P.urllib.request, "urlretrieve") as download:
            code, err, out = call(P.finish, "task_1", os.path.join(d, "o.mp4"), 5)
        return code, err, out, download

    def test_non_https_result_url_is_refused(self):
        for url in ("file:///etc/hostname", "http://insecure.example/a.mp4"):
            code, err, _, download = self.finish(video_result(url))
            self.assertEqual(code, 1, url)
            download.assert_not_called()

    def test_https_result_is_downloaded_and_reported_as_json(self):
        code, _, out, download = self.finish(video_result("https://signed.example/a.mp4?sig=1"))
        self.assertEqual(code, 0)
        download.assert_called_once()
        self.assertEqual(json.loads(out)["taskId"], "task_1")

    def test_failed_task_missing_video_and_unknown_task_are_errors(self):
        self.assertEqual(self.finish(video_result(None, "FAILED"))[0], 1)
        self.assertEqual(self.finish(video_result(None))[0], 1)
        self.assertEqual(self.finish((404, {}))[0], 1)


class GenTests(unittest.TestCase):
    ENV = {"PAPRIKA_PROJECT_ID": "prj_x", "PAPRIKA_API_KEY": "k"}

    def setUp(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"prompt": "p", "duration": 7, "assets": [{"assetId": "media_1", "role": "REFERENCE_IMAGE"}]}, f)
        f.close()
        self.spec = f.name
        self.addCleanup(os.unlink, f.name)

    def gen(self, **kw):
        args = dict(spec=self.spec, out="/tmp/unused.mp4", dry_run=False, timeout=5, idempotency_key=None)
        args.update(kw)
        return call(P.cmd_gen, NS(**args))

    def test_dry_run_prints_the_request_and_never_touches_the_network(self):
        with mock.patch.dict(os.environ, self.ENV, clear=True), NO_NETWORK:
            code, _, out = self.gen(dry_run=True)
        body = json.loads(out)
        self.assertEqual((code, body["projectId"], body["duration"], body["capabilityType"]),
                         (0, "prj_x", 7, "REFERENCE_TO_VIDEO"))
        self.assertEqual(body["inputAssets"], [{"assetId": "media_1", "role": "REFERENCE_IMAGE"}])

    def test_idempotency_key_is_logged_and_can_be_reused(self):
        sent = []

        def fake_http(method, path, body=None, auth=None, headers=None, **kw):
            sent.append((headers or {}).get("Idempotency-Key"))
            return 202, {"data": {"taskId": "task_9", "priceQuote": {"totalAmount": "1.00", "currency": "CNY"}}}

        with mock.patch.dict(os.environ, self.ENV, clear=True), mock.patch.object(P, "http", fake_http), \
                mock.patch.object(P, "finish") as finish:
            _, err1, _ = self.gen()
            _, err2, _ = self.gen(idempotency_key="my-key")
            _, _, _ = self.gen(idempotency_key="my-key")
        self.assertTrue(sent[0].startswith("pk-"))
        self.assertIn("idempotency-key " + sent[0], err1)
        self.assertEqual(sent[1:], ["my-key", "my-key"])
        self.assertIn("--idempotency-key my-key", err2)
        self.assertEqual(finish.call_args.args[0], "task_9")

    def test_submit_failure_is_reported_without_polling(self):
        with mock.patch.dict(os.environ, self.ENV, clear=True), mock.patch.object(P, "http", return_value=(402, {"error": "x"})), \
                mock.patch.object(P, "finish") as finish:
            code, err, _ = self.gen()
        self.assertEqual(code, 1)
        self.assertIn("submit failed", err)
        finish.assert_not_called()

    def test_resume_polls_the_same_task_without_resubmitting(self):
        with mock.patch.object(P, "finish") as finish, NO_NETWORK:
            call(P.cmd_resume, NS(task_id="task_7", out="o.mp4", timeout=9))
        finish.assert_called_once_with("task_7", "o.mp4", 9)


class ShellScriptTests(unittest.TestCase):
    """The shell scripts run against a fake curl/ffmpeg and a temp cache: still fully offline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.cache = d / "cache"
        self.paprika = self.cache / "paprika"
        self.paprika.mkdir(parents=True)
        self.bin = d / "fakebin"
        self.bin.mkdir()
        self.curl_log = d / "curl.log"
        curl = self.bin / "curl"
        curl.write_text('#!/bin/sh\necho "$@" >> "$CURL_LOG"\n[ -n "$CURL_FAIL" ] && exit 22\nwhile [ $# -gt 0 ]; do '
                        '[ "$1" = "-o" ] && { echo junk > "$2"; exit 0; }; shift; done\nexit 22\n')
        curl.chmod(0o755)
        self.ffmpeg = self.bin / "ffmpeg-with-arnndn"
        self.ffmpeg.write_text('#!/bin/sh\ncase "$*" in *-filters*) echo " ... arnndn A->A Reduce noise";; esac\nexit 0\n')
        self.ffmpeg.chmod(0o755)
        self.plain_ffmpeg = self.bin / "ffmpeg-without-arnndn"
        self.plain_ffmpeg.write_text('#!/bin/sh\ncase "$*" in *-filters*) echo " ... anull A->A";; esac\nexit 0\n')
        self.plain_ffmpeg.chmod(0o755)
        self.input = d / "in.wav"
        self.input.write_bytes(b"RIFF")

    def run_script(self, name, *args, **extra):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "XDG_CACHE_HOME": str(self.cache), "CURL_LOG": str(self.curl_log),
               "HOME": self.tmp.name}
        env.update({k: str(v) for k, v in extra.items()})
        return subprocess.run(["bash", str(SCRIPTS / name), *args], env=env, capture_output=True, text=True, timeout=60)

    def test_scripts_answer_help(self):
        for name in ("get_ffmpeg.sh", "prep_voice.sh"):
            r = self.run_script(name, "--help")
            self.assertEqual(r.returncode, 0, name)
            self.assertIn(name, r.stdout)
        r = subprocess.run(["python3", str(SCRIPTS / "paprika.py"), "--help"], capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage:", r.stdout)

    def test_ffmpeg_override_must_have_arnndn(self):
        r = self.run_script("get_ffmpeg.sh", FFMPEG=self.ffmpeg)
        self.assertEqual((r.returncode, json.loads(r.stdout)["source"]), (0, "env"))
        r = self.run_script("get_ffmpeg.sh", FFMPEG=self.plain_ffmpeg)
        self.assertEqual(r.returncode, 1)
        self.assertIn("arnndn", r.stderr)

    def test_tampered_cached_ffmpeg_is_deleted_and_never_used(self):
        cached = self.paprika / "bin" / "ffmpeg"
        cached.parent.mkdir()
        cached.write_text('#!/bin/sh\necho " arnndn "\n')  # has arnndn but the wrong hash
        cached.chmod(0o755)
        r = self.run_script("get_ffmpeg.sh")
        self.assertNotEqual(r.returncode, 0)  # fake curl cannot download a replacement
        self.assertIn("failed verification", r.stderr)
        self.assertFalse(cached.exists())
        self.assertNotIn(str(cached), r.stdout)

    def test_tampered_cached_model_is_deleted_and_redownload_is_hash_checked(self):
        model = self.paprika / "sh.rnnn"
        model.write_text("tampered")
        r = self.run_script("prep_voice.sh", str(self.input), str(Path(self.tmp.name) / "out.wav"), FFMPEG=self.ffmpeg)
        self.assertEqual(r.returncode, 1)
        self.assertIn("model sha256 mismatch", r.stderr)  # fake curl delivered junk
        self.assertTrue(self.curl_log.exists())
        self.assertFalse(model.exists())
        self.assertFalse((self.paprika / "sh.rnnn.part").exists())

    def test_failed_model_download_is_an_error(self):
        r = self.run_script("prep_voice.sh", str(self.input), str(Path(self.tmp.name) / "out.wav"), FFMPEG=self.ffmpeg,
                            CURL_FAIL="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.paprika / "sh.rnnn").exists())

    def test_missing_input_is_an_error(self):
        r = self.run_script("prep_voice.sh", str(self.input) + ".nope", "out.wav", FFMPEG=self.ffmpeg)
        self.assertEqual(r.returncode, 1)
        self.assertIn("input not found", r.stderr)


if __name__ == "__main__":
    unittest.main()
