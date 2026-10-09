"""Retrieve Typeform responses and normalize them for user-research-monitor.

The adapter keeps provider-specific API shapes at the ingestion boundary. Downstream
quality and analysis code receives the monitor's existing response envelope:
``responseId``, ``createTime``, ``lastSubmittedTime`` and text-based ``answers``.

Authentication: set TYPEFORM_ACCESS_TOKEN in the local environment. Never put the
token in cohort_mapping.yaml or project artifacts.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


OFFICIAL_API_BASE = "https://api.typeform.com"


class TypeformAPIError(RuntimeError):
    """Raised when the Typeform API cannot be read safely."""


def _api_base(env: dict[str, str] | None = None) -> str:
    source_env = os.environ if env is None else env
    value = source_env.get("TYPEFORM_API_BASE", OFFICIAL_API_BASE).rstrip("/")
    if value != OFFICIAL_API_BASE:
        raise TypeformAPIError(f"TYPEFORM_API_BASE must be {OFFICIAL_API_BASE}; got {value}")
    return value


def _token(env: dict[str, str] | None = None) -> str:
    source_env = os.environ if env is None else env
    value = source_env.get("TYPEFORM_ACCESS_TOKEN", "").strip()
    if not value:
        raise TypeformAPIError("TYPEFORM_ACCESS_TOKEN is required for a typeform source")
    return value


def api_get(
    endpoint: str,
    params: dict[str, str] | None = None,
    *,
    env: dict[str, str] | None = None,
    opener: Callable = urlopen,
) -> dict:
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{_api_base(env)}{endpoint}{query}",
        headers={"Authorization": f"Bearer {_token(env)}", "Accept": "application/json"},
        method="GET",
    )
    for attempt in range(4):
        try:
            with opener(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            exc.read()
            if exc.code not in {429, 503} or attempt == 3:
                raise TypeformAPIError(
                    f"Typeform GET {endpoint} failed with HTTP {exc.code}; "
                    "response body omitted to avoid exposing survey data"
                ) from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = min(float(retry_after), 30.0) if retry_after else 0.5 * (2**attempt)
            except ValueError:
                delay = 0.5 * (2**attempt)
            time.sleep(delay)
    raise TypeformAPIError(f"Typeform GET {endpoint} exhausted retries")


def flatten_fields(fields: list[dict]) -> list[dict]:
    output: list[dict] = []
    for field in fields or []:
        output.append(field)
        nested = (field.get("properties") or {}).get("fields") or field.get("fields") or []
        if nested:
            output.extend(flatten_fields(nested))
    return output


def extract_schema(form_meta: dict) -> dict[str, dict]:
    """Build provider question ID -> monitor schema metadata."""
    schema: dict[str, dict] = {}
    for field in flatten_fields(form_meta.get("fields") or []):
        qid = field.get("id") or field.get("ref")
        if not qid:
            continue
        field_type = field.get("type", "unknown")
        props = field.get("properties") or {}
        options = [choice.get("label", "") for choice in props.get("choices") or []]
        if field_type in {"multiple_choice", "picture_choice"}:
            allows_multiple = props.get("allow_multiple_selection", props.get("allow_multiple_selections", False))
            qtype = "checkbox" if allows_multiple else "radio"
        elif field_type in {"opinion_scale", "rating", "nps"}:
            qtype = "scale"
            if field_type == "nps":
                options = {"low": 0, "high": 10, "low_label": "", "high_label": ""}
            else:
                start = props.get("start_at", 1)
                steps = props.get("steps")
                options = {
                    "low": start,
                    "high": (start + steps - 1) if isinstance(steps, int) else None,
                    "low_label": props.get("labels", {}).get("left", ""),
                    "high_label": props.get("labels", {}).get("right", ""),
                }
        elif field_type in {"long_text"}:
            qtype = "paragraph"
        elif field_type in {"short_text", "email", "phone_number", "website"}:
            qtype = "short_text"
        else:
            qtype = field_type
        schema[qid] = {
            "title": field.get("title", ""),
            "description": props.get("description", ""),
            "type": qtype,
            "provider_type": field_type,
            "provider_ref": field.get("ref"),
            "options": options or None,
            "required": bool((field.get("validations") or {}).get("required", False)),
        }
    return schema


def _answer_values(answer: dict) -> list[str]:
    if answer.get("choice"):
        choice = answer["choice"]
        return [str(choice.get("label") or choice.get("ref") or choice.get("id") or "")]
    if answer.get("choices"):
        choices = answer["choices"]
        values = choices.get("labels") or choices.get("refs") or choices.get("ids") or []
        return [str(value) for value in values if value is not None]
    for key in ("text", "email", "phone_number", "url", "file_url", "date"):
        if answer.get(key) is not None:
            return [str(answer[key])]
    if answer.get("boolean") is not None:
        return ["true" if answer["boolean"] else "false"]
    if answer.get("number") is not None:
        return [str(answer["number"])]
    if answer.get("payment") is not None:
        return [json.dumps(answer["payment"], ensure_ascii=False, sort_keys=True)]
    return []


def normalize_response(item: dict) -> dict:
    answers: dict[str, dict] = {}
    for answer in item.get("answers") or []:
        field = answer.get("field") or {}
        qid = field.get("id") or field.get("ref")
        values = [value for value in _answer_values(answer) if value != ""]
        if not qid or not values:
            continue
        answers[qid] = {
            "questionId": qid,
            "textAnswers": {"answers": [{"value": value} for value in values]},
        }
    response_id = item.get("response_id") or item.get("token")
    if not response_id:
        raise TypeformAPIError("Typeform response is missing response_id/token")
    return {
        "responseId": response_id,
        "createTime": item.get("landed_at", ""),
        "lastSubmittedTime": item.get("submitted_at") or item.get("staged_at") or "",
        "answers": answers,
        "hidden": item.get("hidden") or {},
        "calculated": item.get("calculated") or item.get("variables") or {},
        "_provider": {"name": "typeform", "token": item.get("token")},
    }


def fetch_all_responses(
    form_id: str,
    *,
    since: str | None = None,
    until: str | None = None,
    response_type: str = "completed",
    getter: Callable = api_get,
) -> list[dict]:
    if response_type not in {"completed", "partial", "started"}:
        raise TypeformAPIError("response_type must be completed, partial, or started")
    collected: list[dict] = []
    after: str | None = None
    while True:
        sort_field = {"completed": "submitted_at", "partial": "staged_at", "started": "landed_at"}[response_type]
        params = {"page_size": "1000", "response_type": response_type, "sort": f"{sort_field},asc"}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if after:
            params["after"] = after
        payload = getter(f"/forms/{quote(form_id, safe='')}/responses", params)
        items = payload.get("items") or []
        collected.extend(items)
        if len(items) < 1000:
            break
        next_cursor = items[-1].get("token") or items[-1].get("response_id")
        if not next_cursor or next_cursor == after:
            raise TypeformAPIError("Typeform pagination did not return a usable next cursor")
        after = next_cursor
    return collected


def fetch_dataset(source: dict, getter: Callable = api_get) -> tuple[dict, list[dict], dict]:
    form_id = str(source.get("form_id") or "").strip()
    if not form_id:
        raise TypeformAPIError("typeform source requires form_id")
    form_meta = getter(f"/forms/{quote(form_id, safe='')}", None)
    raw_items = fetch_all_responses(
        form_id,
        since=source.get("since"),
        until=source.get("until"),
        response_type=source.get("response_type", "completed"),
        getter=getter,
    )
    canonical_meta = {
        "platform": "typeform",
        "form_id": form_id,
        "info": {"title": form_meta.get("title", "")},
        "provider_form": form_meta,
        "retrieval_note": "Typeform documents that very recent responses may take about 30 minutes to appear in the Responses API.",
    }
    return canonical_meta, [normalize_response(item) for item in raw_items], extract_schema(form_meta)


def self_test() -> None:
    form = {
        "id": "abc123",
        "title": "Research survey",
        "fields": [
            {"id": "f1", "ref": "need", "title": "Main need", "type": "multiple_choice", "properties": {"choices": [{"label": "Bird ID", "ref": "bird_id"}]}, "validations": {"required": True}},
            {"id": "f2", "ref": "why", "title": "Why?", "type": "long_text", "properties": {}},
        ],
    }
    item = {
        "token": "resp-1",
        "landed_at": "2026-09-09T01:00:00Z",
        "submitted_at": "2026-09-09T01:02:00Z",
        "answers": [
            {"field": {"id": "f1", "ref": "need"}, "type": "choice", "choice": {"label": "Bird ID", "ref": "bird_id"}},
            {"field": {"id": "f2", "ref": "why"}, "type": "text", "text": "It saves time"},
        ],
    }
    schema = extract_schema(form)
    response = normalize_response(item)
    assert schema["f1"]["type"] == "radio"
    assert schema["f2"]["type"] == "paragraph"
    assert response["responseId"] == "resp-1"
    assert response["answers"]["f1"]["textAnswers"]["answers"][0]["value"] == "Bird ID"
    assert response["answers"]["f2"]["textAnswers"]["answers"][0]["value"] == "It saves time"
    calls: list[tuple[str, dict | None]] = []

    def fake_getter(endpoint: str, params: dict | None = None) -> dict:
        calls.append((endpoint, params))
        if endpoint == "/forms/abc123":
            return form
        return {"total_items": 1, "items": [item]}

    meta, responses, fetched_schema = fetch_dataset(
        {"form_id": "abc123", "response_type": "completed", "since": "2026-09-01T00:00:00Z"},
        getter=fake_getter,
    )
    assert meta["platform"] == "typeform"
    assert len(responses) == 1 and fetched_schema["f1"]["required"] is True
    assert calls[1][1]["response_type"] == "completed"
    assert calls[1][1]["since"] == "2026-09-01T00:00:00Z"
    print("typeform ingestion self-test passed")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Typeform response adapter for user-research-monitor")
    parser.add_argument("command", choices=["self-test"])
    args = parser.parse_args()
    if args.command == "self-test":
        self_test()
