from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "buzz_acp_media_proxy.py"


def load_module():
    spec = importlib.util.spec_from_file_location("buzz_acp_media_proxy", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load media proxy")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuzzAcpMediaProxyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.proxy = load_module()
        self.image = b"\x89PNG\r\n\x1a\n" + b"test-image"
        self.digest = hashlib.sha256(self.image).hexdigest()
        self.url = f"https://relay.example/media/{self.digest}.png"

    def request(self, *, mime: str = "image/png", size: int | None = None) -> dict:
        tag = [
            "imeta",
            f"url {self.url}",
            f"m {mime}",
            f"x {self.digest}",
            f"size {len(self.image) if size is None else size}",
        ]
        return {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/prompt",
            "params": {
                "sessionId": "s1",
                "prompt": [
                    {
                        "type": "text",
                        "text": "Event ID: e1\nContent: inspect\nTags: "
                        + json.dumps([["p", "agent"], tag]),
                    }
                ],
            },
        }

    def test_supported_imeta_becomes_inline_image_without_uri(self) -> None:
        output, stats = self.proxy.augment_prompt(
            self.request(), lambda _item: self.image, image_supported=True
        )
        prompt = output["params"]["prompt"]
        self.assertEqual(stats, {"found": 1, "added": 1, "failed": 0, "bytes": len(self.image)})
        self.assertEqual(prompt[-1]["type"], "image")
        self.assertEqual(prompt[-1]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(prompt[-1]["data"]), self.image)
        self.assertNotIn("uri", prompt[-1])

    def test_adapter_without_image_capability_stays_text_only(self) -> None:
        output, stats = self.proxy.augment_prompt(
            self.request(), lambda _item: self.fail("must not download"), image_supported=False
        )
        self.assertEqual(len(output["params"]["prompt"]), 1)
        self.assertEqual(stats["added"], 0)

    def test_hash_size_and_mime_failures_are_soft(self) -> None:
        for data in (self.image + b"x", b"not-an-image"):
            with self.subTest(data=data):
                output, stats = self.proxy.augment_prompt(
                    self.request(), lambda _item, data=data: data, image_supported=True
                )
                self.assertEqual(len(output["params"]["prompt"]), 1)
                self.assertEqual(stats["failed"], 1)

    def test_non_image_and_malformed_imeta_are_ignored(self) -> None:
        request = self.request(mime="video/mp4")
        output, stats = self.proxy.augment_prompt(
            request, lambda _item: self.fail("must not download"), image_supported=True
        )
        self.assertEqual(len(output["params"]["prompt"]), 1)
        self.assertEqual(stats["found"], 0)

    def test_initialize_capability_is_read_from_adapter_response(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 0,
            "result": {
                "agentCapabilities": {"promptCapabilities": {"image": True}}
            },
        }
        self.assertIs(self.proxy.adapter_image_capability(message), True)

    def test_stdio_proxy_negotiates_then_injects_for_real_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "captured.json"
            adapter = root / "claude-agent-acp"
            adapter.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "for line in sys.stdin:\n"
                " message=json.loads(line)\n"
                " if message.get('method') == 'initialize':\n"
                "  result={'agentCapabilities':{'promptCapabilities':{'image':True}}}\n"
                " else:\n"
                "  open(os.environ['CAPTURE'], 'w').write(json.dumps(message))\n"
                "  result={'stopReason':'end_turn'}\n"
                " print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}), flush=True)\n",
                encoding="utf-8",
            )
            adapter.chmod(0o755)
            buzz = root / "buzz"
            buzz.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "sys.stdout.buffer.write(bytes.fromhex(os.environ['IMAGE_HEX']))\n",
                encoding="utf-8",
            )
            buzz.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": str(adapter),
                    "BUZZ_ACP_MEDIA_BUZZ_CLI": str(buzz),
                    "CAPTURE": str(capture),
                    "IMAGE_HEX": self.image.hex(),
                }
            )
            process = subprocess.Popen(
                [sys.executable, str(SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(
                json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
                + "\n"
            )
            process.stdin.flush()
            initialized = json.loads(process.stdout.readline())
            self.assertIs(
                initialized["result"]["agentCapabilities"]["promptCapabilities"]["image"],
                True,
            )
            process.stdin.write(json.dumps(self.request()) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["result"]["stopReason"], "end_turn")
            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0)
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            delivered = json.loads(capture.read_text(encoding="utf-8"))
            image_block = delivered["params"]["prompt"][-1]
            self.assertEqual(image_block["type"], "image")
            self.assertNotIn("uri", image_block)


if __name__ == "__main__":
    unittest.main()
