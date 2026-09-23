"""Pull and normalize responses for all configured survey cohorts.

通用版：从 project_dir/config/cohort_mapping.yaml 读 cohort 配置，
拉取 raw responses 写到 project_dir/<output_root>/<date>-<batch>/raw/<TIMESTAMP>/。
支持 Google Forms 和 Typeform，并在采集边界转换为同一内部格式。

Usage:
  cd skills/user-research/user-research-monitor
  python -m scripts.ingest_forms --project-dir <path>
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts import ingest_typeform


class GoogleFormsAPIError(RuntimeError):
    """Raised when Google Forms dependencies or API calls fail."""


def load_cohort_mapping(project_dir: Path) -> dict:
    project_dir = project_dir.resolve()
    p = project_dir / "config" / "cohort_mapping.yaml"
    if not p.exists():
        raise FileNotFoundError(
            f"cohort_mapping.yaml not found at {p}. "
            f"复制本 skill 的 templates/cohort_mapping.yaml.template 到项目 config 后填写。"
        )
    with open(p) as f:
        cfg = yaml.safe_load(f) or {}
    if not cfg.get("research_batch") or not cfg.get("output_root"):
        raise ValueError("cohort_mapping.yaml requires research_batch and output_root")
    output_root = Path(str(cfg["output_root"]))
    if output_root.is_absolute():
        raise ValueError("output_root must be relative to the project directory")
    resolved_output = (project_dir / output_root).resolve()
    try:
        relative_output = resolved_output.relative_to(project_dir)
    except ValueError as exc:
        raise ValueError("output_root must stay inside the project directory") from exc
    if str(relative_output) in {"", "."}:
        raise ValueError("output_root must be a child directory, not the project root")
    cfg["output_root"] = str(relative_output)
    cohorts = cfg.get("cohorts")
    if not isinstance(cohorts, list) or not cohorts:
        raise ValueError("cohort_mapping.yaml requires at least one cohort")
    seen = set()
    for index, cohort in enumerate(cohorts):
        if not isinstance(cohort, dict):
            raise ValueError(f"cohorts[{index}] must be a mapping")
        cohort_key = str(cohort.get("cohort_key") or "").strip()
        business_label = str(cohort.get("business_label") or "").strip()
        source = cohort.get("source") or {}
        form_id = str(source.get("form_id") or cohort.get("form_id") or "").strip()
        if not cohort_key or not business_label or not form_id:
            raise ValueError(
                f"cohorts[{index}] requires cohort_key, business_label, and source.form_id"
            )
        if cohort_key in seen:
            raise ValueError(f"duplicate cohort_key: {cohort_key}")
        seen.add(cohort_key)
        if "<" in form_id or ">" in form_id:
            raise ValueError(f"cohort {cohort_key} still contains a form_id placeholder")
        if source.get("type", "google_form") not in {"google_form", "typeform"}:
            raise ValueError(
                f"cohort {cohort_key} source.type must be google_form or typeform"
            )
    return cfg


def get_snapshot_root(project_dir: Path, date_str: str | None = None) -> Path:
    project_dir = project_dir.resolve()
    cfg = load_cohort_mapping(project_dir)
    base = project_dir / cfg["output_root"]
    batch = cfg["research_batch"]
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return base / f"{date_str}-{batch}"


def fetch_form_metadata(service, form_id: str) -> dict:
    return service.forms().get(formId=form_id).execute()


def fetch_all_responses(service, form_id: str) -> list[dict]:
    responses: list[dict] = []
    page_token: str | None = None
    while True:
        kwargs = {"formId": form_id, "pageSize": 100}
        if page_token:
            kwargs["pageToken"] = page_token
        result = service.forms().responses().list(**kwargs).execute()
        responses.extend(result.get("responses", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return responses


def extract_schema(form_meta: dict) -> dict:
    """Build questionId → {title, type, options} mapping."""
    schema = {}
    for item in form_meta.get("items", []):
        question = item.get("questionItem", {}).get("question", {})
        if not question:
            continue
        qid = question.get("questionId")
        if not qid:
            continue
        qtype = "unknown"
        options = None
        if "choiceQuestion" in question:
            qtype = question["choiceQuestion"].get("type", "RADIO").lower()
            options = [o.get("value") for o in question["choiceQuestion"].get("options", [])]
        elif "scaleQuestion" in question:
            qtype = "scale"
            sc = question["scaleQuestion"]
            options = {
                "low": sc.get("low"),
                "high": sc.get("high"),
                "low_label": sc.get("lowLabel"),
                "high_label": sc.get("highLabel"),
            }
        elif "textQuestion" in question:
            qtype = "paragraph" if question["textQuestion"].get("paragraph") else "short_text"
        elif "ratingQuestion" in question:
            qtype = "rating"
        schema[qid] = {
            "title": item.get("title", ""),
            "description": item.get("description", ""),
            "type": qtype,
            "options": options,
            "required": question.get("required", False),
        }
    return schema


def write_discovered_schema(project_dir: Path, all_schemas: dict[str, dict]) -> Path:
    """写 project_dir/config/discovered_schema.yaml。"""
    out = project_dir / "config" / "discovered_schema.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "首次拉取后自动生成。PM 检查每个 cohort 的题目顺序与文案是否符合预期。"
            "下游脚本（enrich_schema / quality_engine / project analyze.py）"
            "通过项目定义的 logical_qid 匹配题目；映射由 enrich_schema.py 确定性写入。"
        ),
        "cohorts": all_schemas,
    }
    with open(out, "w") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
    return out


def run(project_dir: Path, date: str | None = None):
    """从已配置的问卷平台拉所有 cohort responses 到 project_dir。"""
    cfg = load_cohort_mapping(project_dir)
    google_service = None

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    snapshot_root = get_snapshot_root(project_dir, date)
    out_dir = snapshot_root / "raw" / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    all_schemas: dict[str, dict] = {}
    summary_rows = []
    failures = []

    for cohort in cfg["cohorts"]:
        ck = cohort["cohort_key"]
        bl = cohort["business_label"]
        source = cohort.get("source") or {}
        source_type = source.get("type", "google_form")
        # 兼容两种 schema：source.form_id（新模板）或顶层 form_id（老 cohort_mapping）
        fid = (source.get("form_id") or cohort.get("form_id") or "").strip()
        print(f"→ Pulling {ck} ({bl})  source={source_type}  form_id={fid[:12]}…")
        try:
            if source_type == "google_form":
                try:
                    if google_service is None:
                        from googleapiclient.discovery import build
                        from scripts.auth import get_credentials

                        google_service = build(
                            "forms", "v1", credentials=get_credentials()
                        )
                    form_meta = fetch_form_metadata(google_service, fid)
                    responses = fetch_all_responses(google_service, fid)
                    schema = extract_schema(form_meta)
                    platform = "google_forms"
                except ImportError as exc:
                    raise GoogleFormsAPIError(
                        "Google Forms source requires the optional dependencies in "
                        "user-research-monitor/requirements.txt"
                    ) from exc
                except Exception as exc:
                    raise GoogleFormsAPIError(
                        f"Google Forms API request failed: {exc}"
                    ) from exc
            elif source_type == "typeform":
                form_meta, responses, schema = ingest_typeform.fetch_dataset(source)
                platform = "typeform"
            else:
                raise ValueError(
                    f"unsupported source.type={source_type}; use google_form or typeform "
                    "(other platforms currently require a separately normalized import)"
                )
        except (GoogleFormsAPIError, ingest_typeform.TypeformAPIError, ValueError) as e:
            print(f"  ✗ {source_type} ingestion error: {e}")
            summary_rows.append((ck, bl, "ERR", str(e)[:60]))
            failures.append(f"{ck}: {e}")
            continue

        raw_path = out_dir / f"{ck}.json"
        with open(raw_path, "w") as f:
            json.dump(
                {"form_meta": form_meta, "responses": responses},
                f,
                ensure_ascii=False,
                indent=2,
            )
        all_schemas[ck] = {
            "platform": platform,
            "form_id": fid,
            "business_label": bl,
            "form_title": form_meta.get("info", {}).get("title", ""),
            "questions": schema,
        }
        try:
            rel = raw_path.relative_to(project_dir)
        except ValueError:
            rel = raw_path
        print(f"  ✓ {len(responses)} responses → {rel}")
        summary_rows.append((ck, bl, len(responses), "OK"))

    if all_schemas:
        schema_path = write_discovered_schema(project_dir, all_schemas)
        try:
            rel = schema_path.relative_to(project_dir)
        except ValueError:
            rel = schema_path
        print(f"\n✓ Discovered schema → {rel}")

    print("\n=== Ingest Summary ===")
    print(f"{'Cohort':<20} {'Business Label':<24} {'Count':>6}  Status")
    for row in summary_rows:
        ck, bl, cnt, status = row
        print(f"{ck:<20} {bl:<24} {str(cnt):>6}  {status}")

    if failures:
        raise RuntimeError(
            "one or more configured cohorts failed ingestion: " + "; ".join(failures)
        )

    return out_dir, summary_rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pull normalized survey responses for project cohorts")
    parser.add_argument("--project-dir", required=True, type=Path, help="Project root with config/cohort_mapping.yaml")
    parser.add_argument("--date", default=None, help="Snapshot date YYYY-MM-DD (default: today UTC)")
    args = parser.parse_args()
    run(args.project_dir, args.date)
