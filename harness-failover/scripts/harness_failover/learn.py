"""Unknown-error learning: suspicious error lines no signature explains are redacted, de-duplicated and
turned into a reviewable proposal (signature stub + fixture stub). Samples end up in a merge request,
so redaction errs on the side of removing too much."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile

from .signatures import classify

_REDACTIONS = [
    # whole header / cookie lines first: their values contain spaces and '=' that the generic rules miss
    (re.compile(r"\b(authorization|proxy-authorization|cookie|set-cookie)\b\s*[:=][^\r\n]*", re.I), r"\1: <redacted>"),
    (re.compile(r"Bearer:?\s+[A-Za-z0-9._~+/=\-]+", re.I), "Bearer <redacted>"),
    (re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{6,}"), "Basic <redacted>"),
    (re.compile(r"\b(?:nsec|npub)1[a-z0-9]{20,}\b"), "<nostr-key>"),
    (re.compile(r"\b(?:glpat|ghp|gho|ghs|ghu|ghr|xox[abps])[-_][A-Za-z0-9_\-]{10,}"), "<token>"),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{8,}"), "<token>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"), "<token>"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<aws-key>"),
    (re.compile(r"(?<=://)[^/\s:@]*:[^/\s@]+@"), "<redacted>@"),  # scheme://user:pass@host
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<email>"),
    # key names may be quoted (JSON) or carry prefixes/suffixes/spaces: access_token, client_secret, "api_key", api key
    (re.compile(r"(?P<key>[\"']?[\w-]*(?:token|secret|password|passwd|pwd|passphrase|api[ _-]?key|private[ _-]?key|"
                r"credential|session[ _-]?id)[\w-]*[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s\"',;}]+)", re.I),
     r"\g<key><redacted>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{64}\b"), "<hex64>"),
    (re.compile(r"\b[A-Za-z0-9+/_\-]{32,}\b"), "<token>"),
]
_TS = re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+Z?")
MAX_SAMPLE = 400


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def normalize(text: str) -> str:
    t = _TS.sub("<ts>", redact(text))
    t = re.sub(r"\d+", "N", t)
    return re.sub(r"\s+", " ", t).strip()


def load_unknown(path) -> list:
    out = []
    try:
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a corrupt line must not take the whole learning store (and the run) down
    except OSError:
        return []
    return out


def _write(path, entries) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".unknown.")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def record_unknown(lines, path, now) -> int:
    entries = {e["key"]: e for e in load_unknown(path)}
    stamp, new = now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        key = hashlib.sha1(normalize(line).encode()).hexdigest()[:12]
        if key in entries:
            entries[key]["count"] += 1
            entries[key]["last_seen"] = stamp
        else:
            entries[key] = {"key": key, "count": 1, "first_seen": stamp, "last_seen": stamp,
                            "sample": redact(line)[:MAX_SAMPLE]}
            new += 1
    _write(path, list(entries.values()))
    return new


def prune_known(path, sigs) -> int:
    entries = load_unknown(path)
    keep = [e for e in entries if classify(e["sample"], sigs) is None]
    if len(keep) != len(entries):
        _write(path, keep)
    return len(entries) - len(keep)


def _phrase(sample: str) -> str:
    m = re.search(r"(?:error_message|error|message)=(.+)$", sample)
    return (m.group(1) if m else sample[-80:]).strip()[:80]


def propose(entries) -> str:
    if not entries:
        return "No unclassified error samples recorded.\n"
    out = []
    for i, e in enumerate(entries, 1):
        sig = {"id": f"<unknown-{i}>", "harness": "<grok|claude|codex>", "kind": "quota|transient|auth",
               "pattern": re.escape(_phrase(e["sample"])), "reset": "none", "verified": False}
        fixture = {"sig": f"<unknown-{i}>", "text": e["sample"]}
        out.append(
            f"## Unclassified error {i}  (seen {e['count']}x, {e['first_seen']} → {e['last_seen']})\n"
            f"sample (redacted): {e['sample']}\n\n"
            f"1) assets/signatures.json — add (pick id/harness/kind; add a `reset` rule if the text has a reset time):\n"
            f"   {json.dumps(sig, ensure_ascii=False)}\n"
            f"2) tests/fixtures/samples.json — add the sample as a fixture:\n"
            f"   {json.dumps(fixture, ensure_ascii=False)}\n"
            f"3) `uv run --extra dev pytest skills/harness-failover/tests -q`, update SKILL.md's signature table, open an MR "
            f"(references/self-update.md)\n")
    return "\n".join(out)
