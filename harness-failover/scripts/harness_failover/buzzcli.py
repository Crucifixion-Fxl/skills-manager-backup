"""Thin wrapper around the buzz CLI.

`as_owner=True` runs the CLI with every BUZZ_* variable stripped, so the standard `buzz` wrapper loads the local
user's (owner) identity instead of the calling agent's. Use it only for the few calls that need the human's
authority (reading a channel the agent cannot vouch for, and the retry nudge). Tests inject a fake via BUZZ_CLI."""
from __future__ import annotations

import json
import os
import subprocess

CODE_CATEGORY = {1: "input", 2: "network", 3: "auth", 4: "other", 5: "conflict"}


class BuzzCliError(RuntimeError):
    def __init__(self, message, code=None, category=None):
        super().__init__(message)
        self.code = code
        self.category = category


class BuzzCli:
    def __init__(self, path=None, home=None, environ=None):
        self.environ = dict(os.environ if environ is None else environ)
        self.home = home or os.path.expanduser("~")
        self.path = path or self.environ.get("BUZZ_CLI") or os.path.join(self.home, ".local/bin/buzz")

    def _env(self, as_owner: bool) -> dict:
        if as_owner:
            return {"HOME": self.home, "PATH": self.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        return dict(self.environ)

    def run(self, args, as_owner=False, timeout=60):
        if not os.path.exists(self.path):
            raise BuzzCliError(f"buzz CLI not found: {self.path}", category="missing")
        try:
            p = subprocess.run([self.path, *args], env=self._env(as_owner), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise BuzzCliError("buzz CLI timed out", category="timeout") from e
        if p.returncode != 0:
            category, message = CODE_CATEGORY.get(p.returncode, "other"), p.stderr.strip()[:200]
            try:
                err = json.loads(p.stderr)
                category, message = err.get("error") or category, err.get("message") or message
            except ValueError:
                pass
            raise BuzzCliError(message, code=p.returncode, category=category)
        try:
            return json.loads(p.stdout)
        except ValueError as e:
            raise BuzzCliError("buzz CLI returned non-JSON output", category="parse") from e

    def messages_get(self, channel, since, limit=200, as_owner=True):
        return self.run(["messages", "get", "--channel", channel, "--since", str(since), "--limit", str(limit)],
                        as_owner=as_owner)

    def messages_send(self, channel, content, reply_to, mention, as_owner=True):
        return self.run(["messages", "send", "--channel", channel, "--content", content, "--reply-to", reply_to,
                         "--mention", mention], as_owner=as_owner)
