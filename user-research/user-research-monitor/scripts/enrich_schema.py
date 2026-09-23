"""Apply deterministic project-owned logical question IDs to discovered schema."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml


class SchemaMappingError(ValueError):
    """Raised when logical question mapping is incomplete or ambiguous."""


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise SchemaMappingError(f"{path} must contain a YAML mapping")
    return value


def load_overrides(project_dir: Path) -> dict:
    path = project_dir / "config" / "schema_overrides.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"schema_overrides.yaml not found at {path}; create it from the skill template"
        )
    config = _load_yaml(path)
    if config.get("version") != 1:
        raise SchemaMappingError("schema_overrides.yaml version must be 1")
    logical_questions = config.get("logical_questions")
    if not isinstance(logical_questions, dict) or not logical_questions:
        raise SchemaMappingError("logical_questions must be a non-empty mapping")
    return config


def _title_index(logical_questions: dict) -> dict[str, str]:
    index: dict[str, str] = {}
    for logical_qid, spec in logical_questions.items():
        if not isinstance(spec, dict):
            raise SchemaMappingError(f"logical question {logical_qid} must be a mapping")
        for title in spec.get("exact_titles") or []:
            key = normalize(str(title))
            if not key:
                raise SchemaMappingError(f"logical question {logical_qid} has a blank exact title")
            if key in index and index[key] != logical_qid:
                raise SchemaMappingError(
                    f"exact title is assigned to both {index[key]} and {logical_qid}: {title!r}"
                )
            index[key] = str(logical_qid)
    return index


def enrich(project_dir: Path) -> dict:
    schema_path = project_dir / "config" / "discovered_schema.yaml"
    if not schema_path.exists():
        raise FileNotFoundError(
            f"discovered_schema.yaml not found at {schema_path}; ingest responses first"
        )
    schema = _load_yaml(schema_path)
    config = load_overrides(project_dir)
    logical_questions = config["logical_questions"]
    title_index = _title_index(logical_questions)
    allow_unmapped = bool(config.get("allow_unmapped", True))
    summary: list[dict] = []

    for cohort_key, cohort in (schema.get("cohorts") or {}).items():
        questions = (cohort or {}).get("questions") or {}
        explicit: dict[str, str] = {}
        for logical_qid, spec in logical_questions.items():
            provider_by_cohort = (spec or {}).get("provider_question_ids_by_cohort") or {}
            provider_qid = provider_by_cohort.get(cohort_key)
            if provider_qid:
                if provider_qid in explicit and explicit[provider_qid] != logical_qid:
                    raise SchemaMappingError(
                        f"provider question {provider_qid} in {cohort_key} maps to multiple logical IDs"
                    )
                explicit[str(provider_qid)] = str(logical_qid)

        mapped = 0
        unmapped = 0
        seen_logical: dict[str, str] = {}
        for provider_qid, question in questions.items():
            logical_qid = explicit.get(str(provider_qid))
            if logical_qid is None:
                logical_qid = title_index.get(normalize(str((question or {}).get("title", ""))))
            if logical_qid:
                if logical_qid in seen_logical:
                    raise SchemaMappingError(
                        f"cohort {cohort_key} maps both {seen_logical[logical_qid]} and "
                        f"{provider_qid} to logical ID {logical_qid}"
                    )
                question["logical_qid"] = logical_qid
                seen_logical[logical_qid] = str(provider_qid)
                mapped += 1
            else:
                question.pop("logical_qid", None)
                unmapped += 1
        if unmapped and not allow_unmapped:
            raise SchemaMappingError(
                f"cohort {cohort_key} has {unmapped} unmapped questions and allow_unmapped=false"
            )
        summary.append({
            "cohort_key": str(cohort_key),
            "questions": len(questions),
            "mapped": mapped,
            "unmapped": unmapped,
        })

    schema_path.write_text(
        yaml.safe_dump(schema, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return {"schema_path": str(schema_path), "cohorts": summary}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply exact, project-owned logical question mappings"
    )
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(enrich(args.project_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
