"""AWS public pricing API cache — Compute SP + EC2/Lambda/Fargate OD rates.

See spec 7.1–7.8.
"""

from __future__ import annotations

import gzip
import json
import logging
import multiprocessing
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from scripts._common import (
    VERSION,
    CacheError,
    NeedsCacheRefreshError,
    RatioEntry,
    Ratios,
    RegionVersionInfo,
    VersionMetadata,
    _parse_iso,
)

logger = logging.getLogger(__name__)

# Skill-shipped pricing cache — ships with the skill at skills/aws-sp-optimizer/data/.
# Updated by the maintainer via a `--refresh-cache` run + git commit.
SHIPPED_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SHIPPED_RATIOS_PATH = SHIPPED_DATA_DIR / "ratios.json.gz"
SHIPPED_VERSION_PATH = SHIPPED_DATA_DIR / "version.json"

CACHE_SCHEMA_VERSION = 1

STALE_THRESHOLD = timedelta(days=365)


def _parse_version_ts(v: str) -> datetime:
    """Parse AWS pricing version 'YYYYMMDDHHMMSS' → tz-aware UTC datetime."""
    return datetime.strptime(v, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def _is_version_stale(
    cached_ver: str, upstream_ver: str, threshold: timedelta = STALE_THRESHOLD
) -> bool:
    """Return True iff upstream is more than `threshold` newer than cached."""
    return _parse_version_ts(upstream_ver) - _parse_version_ts(cached_ver) > threshold


def _ratios_path() -> Path:
    return SHIPPED_RATIOS_PATH


def _version_path() -> Path:
    return SHIPPED_VERSION_PATH


def write_ratios_atomic(path: Path, content: dict) -> None:
    """Atomic gzip JSON write: tempfile + os.replace. Spec 7.5 'Concurrency and atomic writes'."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(content, f, default=_json_default)
    os.replace(tmp, path)


def write_version_metadata_atomic(path: Path, content: dict) -> None:
    """Atomic plaintext JSON write for version.json (human-readable, not gzipped)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(content, indent=2, default=_json_default))
    os.replace(tmp, path)


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj).__name__}")


def ratios_to_json(ratios: Ratios) -> dict:
    """Convert Ratios dataclass to the JSON schema in spec 7.4.

    Note the 'lambda' vs 'lambda_rates' naming translation — JSON uses
    'lambda' (matching AWS), Python uses 'lambda_rates' (reserved word).
    """
    return {
        "schema_version": ratios.schema_version,
        "term": ratios.term,
        "built_at": ratios.built_at.isoformat(),
        "ec2": {
            region: {key: asdict(entry) for key, entry in table.items()}
            for region, table in ratios.ec2.items()
        },
        "lambda": {key: asdict(entry) for key, entry in ratios.lambda_rates.items()},
        "fargate": {
            region: {key: asdict(entry) for key, entry in table.items()}
            for region, table in ratios.fargate.items()
        },
        "built_from_versions": ratios.built_from_versions,
    }


def version_metadata_to_json(meta: VersionMetadata) -> dict:
    return {
        "schema_version": meta.schema_version,
        "last_refreshed_at": meta.last_refreshed_at.isoformat(),
        "aws_sp_optimizer_version": meta.aws_sp_optimizer_version,
        "regions": {
            region: {
                "sp_version": info.sp_version,
                "od_version": info.od_version,
                "sp_refreshed_at": info.sp_refreshed_at.isoformat(),
                "od_refreshed_at": info.od_refreshed_at.isoformat(),
                "ratio_entries_count": info.ratio_entries_count,
            }
            for region, info in meta.regions.items()
        },
        "lambda_refreshed_at": meta.lambda_refreshed_at.isoformat(),
        "fargate_refreshed_at": meta.fargate_refreshed_at.isoformat(),
    }


def load_and_validate_ratios(primary_region: str) -> Ratios:
    """Read ratios.json.gz and return a Ratios instance.

    Reads from the skill-shipped gzip cache at skills/aws-sp-optimizer/data/ratios.json.gz.
    Maintainers update it via a --refresh-cache run + git commit.

    Raises CacheError on missing file, JSON parse failure, or schema mismatch.
    """
    path = _ratios_path()
    if not path.is_file():
        raise CacheError(
            code="ratios_missing",
            message=f"No ratios cache at {SHIPPED_RATIOS_PATH}",
            context={"shipped_path": str(SHIPPED_RATIOS_PATH)},
        )
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise CacheError(
            code="pricing_json_corrupt",
            message=f"Failed to parse {path}: {e}",
            context={"path": str(path)},
            user_fix_options=[f"Delete {path} and rerun without --skip-pricing-refresh"],
        ) from e

    if not isinstance(raw, dict) or raw.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise CacheError(
            code="pricing_json_corrupt",
            message=f"schema_version mismatch in {path}",
            context={
                "path": str(path),
                "got": raw.get("schema_version") if isinstance(raw, dict) else None,
            },
        )

    ec2 = {
        region: {key: RatioEntry(**entry) for key, entry in table.items()}
        for region, table in raw.get("ec2", {}).items()
    }
    lambda_rates = {key: RatioEntry(**entry) for key, entry in raw.get("lambda", {}).items()}
    fargate = {
        region: {key: RatioEntry(**entry) for key, entry in table.items()}
        for region, table in raw.get("fargate", {}).items()
    }

    return Ratios(
        schema_version=raw["schema_version"],
        term=raw["term"],
        built_at=_parse_iso(raw["built_at"]),
        ec2=ec2,
        lambda_rates=lambda_rates,
        fargate=fargate,
        built_from_versions=raw["built_from_versions"],
        primary_region=primary_region,
    )


def load_version_metadata() -> VersionMetadata | None:
    """Return VersionMetadata or None if file is missing. Raises CacheError on corruption."""
    path = _version_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise CacheError(
            code="pricing_json_corrupt",
            message=f"Failed to parse {path}: {e}",
        ) from e
    regions = {
        name: RegionVersionInfo(
            sp_version=info["sp_version"],
            od_version=info["od_version"],
            sp_refreshed_at=_parse_iso(info["sp_refreshed_at"]),
            od_refreshed_at=_parse_iso(info["od_refreshed_at"]),
            ratio_entries_count=info["ratio_entries_count"],
        )
        for name, info in raw.get("regions", {}).items()
    }
    return VersionMetadata(
        schema_version=raw["schema_version"],
        last_refreshed_at=_parse_iso(raw["last_refreshed_at"]),
        aws_sp_optimizer_version=raw["aws_sp_optimizer_version"],
        regions=regions,
        lambda_refreshed_at=_parse_iso(raw["lambda_refreshed_at"]),
        fargate_refreshed_at=_parse_iso(raw["fargate_refreshed_at"]),
    )


def stream_download_json(
    url: str, chunk_size: int = 1024 * 1024, timeout: int = 300
) -> dict[str, Any]:
    """Download a large JSON file over HTTPS, streaming to a tempfile, then parse.

    Raises requests.exceptions.RequestException subclasses on network error.
    Raises json.JSONDecodeError on parse failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="wb") as tmp:
        tmp_path = Path(tmp.name)
        try:
            with requests.get(url, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                total_str = resp.headers.get("content-length")
                total = int(total_str) if total_str else 0
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        tmp.write(chunk)
                        downloaded += len(chunk)
                        if total and downloaded % (10 * chunk_size) == 0:
                            pct = downloaded / total * 100
                            logger.info(
                                "Download %s: %.0f MB (%.0f%%)",
                                url.rsplit("/", 1)[-1],
                                downloaded / 1024 / 1024,
                                pct,
                            )
            tmp.flush()
        finally:
            pass  # we handle cleanup below
        try:
            result: dict[str, Any] = json.loads(tmp_path.read_text())
            return result
        finally:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Task 2.7: Region index fetchers
# ---------------------------------------------------------------------------

_PRICING_BASE = "https://pricing.us-east-1.amazonaws.com"

SP_REGION_INDEX_URL = (
    f"{_PRICING_BASE}/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/current/region_index.json"
)
EC2_OD_REGION_INDEX_URL = f"{_PRICING_BASE}/offers/v1.0/aws/AmazonEC2/current/region_index.json"
LAMBDA_OD_REGION_INDEX_URL = f"{_PRICING_BASE}/offers/v1.0/aws/AWSLambda/current/region_index.json"
ECS_OD_REGION_INDEX_URL = f"{_PRICING_BASE}/offers/v1.0/aws/AmazonECS/current/region_index.json"


def _extract_version_from_url(url: str) -> str:
    """Given .../savingsPlan/v1.0/aws/AWSComputeSavingsPlan/20260410/us-east-1/index.json
    return '20260410'."""
    parts = url.strip("/").split("/")
    # The version is the component after the offer code (AWSComputeSavingsPlan, AmazonEC2, etc.)
    # Structure: offers|savingsPlan / v1.0 / aws / <offer> / <version> / <region> / index.json
    return parts[-3]


def _fetch_sp_region_index(url: str, required_regions: set[str]) -> dict[str, str]:
    """Parse Compute SP region_index: {"regions": [{"regionCode": ..., "versionUrl": ...}]}.

    Real AWS shape (verified 2026-04-14): regions is a list, field name is "versionUrl".
    Raises CacheError with code "sp_region_index_shape_changed" if shape differs.
    """
    raw = stream_download_json(url)
    regions = raw.get("regions", [])
    if not isinstance(regions, list):
        raise CacheError(
            code="sp_region_index_shape_changed",
            message=f"Expected list-shaped regions for SP index, got {type(regions).__name__}",
            context={"url": url},
        )
    by_code: dict[str, str] = {}
    for entry in regions:
        code = entry.get("regionCode")
        version_url = entry.get("versionUrl", "")
        if code and version_url:
            by_code[code] = _extract_version_from_url(version_url)
    missing = sorted(set(required_regions) - by_code.keys())
    if missing:
        raise CacheError(
            code="region_not_in_sp_index",
            message=f"Required regions {missing} missing from sp index",
            context={"url": url, "missing_regions": missing},
            user_fix_options=[
                "Verify the region code is correct",
                "Compute SP is not offered in some Gov Cloud / China partitions",
            ],
        )
    return {region: by_code[region] for region in required_regions}


def _fetch_offer_region_index(url: str, required_regions: set[str], kind: str) -> dict[str, str]:
    """Parse EC2/Lambda/ECS region_index: {"regions": {region_code: {"currentVersionUrl": ...}}}.

    Real AWS shape (verified 2026-04-14): regions is a dict keyed by region code.
    Raises CacheError with code "<kind>_region_index_shape_changed" if shape differs.
    """
    raw = stream_download_json(url)
    regions = raw.get("regions", {})
    if not isinstance(regions, dict):
        raise CacheError(
            code=f"{kind}_region_index_shape_changed",
            message=f"Expected dict-shaped regions for {kind} index, got {type(regions).__name__}",
            context={"url": url},
        )
    result: dict[str, str] = {}
    missing: list[str] = []
    for region in required_regions:
        entry = regions.get(region)
        if entry is None:
            missing.append(region)
            continue
        version_url = entry.get("currentVersionUrl", "")
        result[region] = _extract_version_from_url(version_url)
    if missing:
        raise CacheError(
            code=f"region_not_in_{kind}_index",
            message=f"Required regions {sorted(missing)} missing from {kind} index",
            context={"url": url, "missing_regions": sorted(missing)},
            user_fix_options=[
                "Verify the region code is correct",
                "Gov Cloud / China regions may not be in the public offers index",
            ],
        )
    return result


def fetch_sp_region_versions(required_regions: set[str]) -> dict[str, str]:
    """Download the Compute SP region index and return {region: version}."""
    return _fetch_sp_region_index(SP_REGION_INDEX_URL, required_regions)


def fetch_od_region_versions(required_regions: set[str]) -> dict[str, str]:
    """Download the EC2 OD region index and return {region: version}."""
    return _fetch_offer_region_index(EC2_OD_REGION_INDEX_URL, required_regions, "od")


# ---------------------------------------------------------------------------
# Task 2.8: build_cache EC2 flow
# ---------------------------------------------------------------------------


def _sp_rate_url(region: str, version: str) -> str:
    return (
        f"{_PRICING_BASE}/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/{version}/{region}/index.json"
    )


def _ec2_od_rate_url(region: str, version: str) -> str:
    return f"{_PRICING_BASE}/offers/v1.0/aws/AmazonEC2/{version}/{region}/index.json"


def _lambda_od_rate_url(region: str, version: str) -> str:
    return f"{_PRICING_BASE}/offers/v1.0/aws/AWSLambda/{version}/{region}/index.json"


def _ecs_od_rate_url(region: str, version: str) -> str:
    return f"{_PRICING_BASE}/offers/v1.0/aws/AmazonECS/{version}/{region}/index.json"


def _locate_1y_nu_compute_term(sp_rate_sheet: dict) -> dict:
    """Find the 1y No Upfront Compute Savings Plan term in a downloaded sheet.

    Real AWS Compute SP rate sheet shape (verified 2026-04-14):
        {"terms": {"savingsPlan": [
            {"sku": "...", "description": "1 year No Upfront Compute Savings Plan",
             "leaseContractLength": {...}, "rates": [{"discountedSku", ...}, ...]},
            ...
        ]}}

    Raises CacheError with code='term_not_found' if no matching term is found,
    or code='sp_sheet_shape_changed' if terms.savingsPlan is not a list.
    """
    target = "1 year No Upfront Compute Savings Plan"
    terms = sp_rate_sheet.get("terms", {}).get("savingsPlan", [])
    if not isinstance(terms, list):
        raise CacheError(
            code="sp_sheet_shape_changed",
            message=(
                f"Expected terms.savingsPlan to be a list, got "
                f"{type(terms).__name__}"
            ),
            context={"real_type": type(terms).__name__},
        )
    for term in terms:
        if term.get("description") == target:
            return dict(term)
    descriptions_seen = [t.get("description", "") for t in terms[:10]]
    raise CacheError(
        code="term_not_found",
        message=f"'{target}' term not found in SP rate sheet",
        context={"descriptions_seen": descriptions_seen},
    )


def _extract_ec2_od_rate(od_sheet: dict, sku: str) -> tuple[float, dict] | None:
    """Return (od_rate_usd, product_attributes) for an EC2 SKU, or None if missing."""
    products = od_sheet.get("products", {})
    terms_od = od_sheet.get("terms", {}).get("OnDemand", {})
    sku_terms = terms_od.get(sku, {})
    if not sku_terms:
        return None
    # Pick first offer term
    first_term = next(iter(sku_terms.values()))
    price_dims = first_term.get("priceDimensions", {})
    if not price_dims:
        return None
    first_pd = next(iter(price_dims.values()))
    try:
        price = float(first_pd["pricePerUnit"]["USD"])
    except (KeyError, ValueError):
        return None
    attributes = products.get(sku, {}).get("attributes", {})
    return price, attributes


def _extract_product_od_rate_by_key(
    od_sheet: dict, key_attribute: str, key_value: str
) -> float | None:
    """Generic: find a product whose attributes[key_attribute] == key_value and
    return its OD USD rate. Used for Lambda (group=usage_type) and Fargate
    (usagetype=usage_type) where we don't join by SKU."""
    products = od_sheet.get("products", {})
    terms_od = od_sheet.get("terms", {}).get("OnDemand", {})
    for sku, product in products.items():
        if product.get("attributes", {}).get(key_attribute) == key_value:
            sku_terms = terms_od.get(sku, {})
            if not sku_terms:
                continue
            first_term = next(iter(sku_terms.values()))
            price_dims = first_term.get("priceDimensions", {})
            if not price_dims:
                continue
            first_pd = next(iter(price_dims.values()))
            try:
                return float(first_pd["pricePerUnit"]["USD"])
            except (KeyError, ValueError):
                continue
    return None


def _extract_sp_ec2_rates(term: dict) -> dict[str, tuple[float, str]]:
    """Phase 1 helper: map {discounted_sku: (sp_price, operation)} for EC2 SKUs only."""
    rates: dict[str, tuple[float, str]] = {}
    for rate in term.get("rates", []):
        if rate.get("discountedServiceCode") != "AmazonEC2":
            continue
        sku = rate.get("discountedSku", "")
        operation = rate.get("discountedOperation", "")
        try:
            sp_price = float(rate["discountedRate"]["price"])
        except (KeyError, ValueError):
            continue
        if sku:
            rates[sku] = (sp_price, operation)
    return rates


def _build_region_ec2_ratios(
    ec2_sheet: dict, sp_ec2_rates: dict[str, tuple[float, str]]
) -> dict[str, RatioEntry]:
    """Phase 2 helper: join SP prices against EC2 OD prices to produce RatioEntry map."""
    region_ec2: dict[str, RatioEntry] = {}
    for sku, (sp_price, operation) in sp_ec2_rates.items():
        extracted = _extract_ec2_od_rate(ec2_sheet, sku)
        if extracted is None:
            continue
        od_price, attributes = extracted
        if od_price <= 0:
            continue
        instance_type = attributes.get("instanceType")
        if not instance_type:
            continue
        ratio = sp_price / od_price
        region_ec2[f"{instance_type}|{operation}"] = RatioEntry(
            sp_rate_usd=sp_price,
            od_rate_usd=od_price,
            ratio=ratio,
            discount_pct=1.0 - ratio,
            sku=sku,
            os=attributes.get("operatingSystem"),
            tenancy=attributes.get("tenancy"),
        )
    return region_ec2


def _build_region_ratios(
    region: str, sp_version: str, od_version: str
) -> tuple[dict[str, RatioEntry], dict[str, RatioEntry] | None, dict[str, RatioEntry]]:
    """Build (ec2_ratios, lambda_rates_or_None, fargate_ratios) for a single region.

    Memory strategy (P7.2 Option B): sequential extract with explicit del so the
    ~1.2 GB SP sheet is released before loading the ~1.5 GB EC2 OD sheet.
    Peak per region ≈ max(SP, EC2) ≈ 1.5 GB instead of ~2.7 GB.
    """
    logger.info("[pricing_cache] Building region %s", region)

    # Phase 1: SP sheet — extract everything we need, then drop the ~1.2 GB dict.
    sp_sheet = stream_download_json(_sp_rate_url(region, sp_version))
    term = _locate_1y_nu_compute_term(sp_sheet)
    sp_ec2_rates = _extract_sp_ec2_rates(term)
    lambda_rates_region = _build_lambda_rates(sp_sheet, region)
    fargate_region = _build_fargate_rates(sp_sheet, region)
    del sp_sheet, term  # Release ~1.2 GB before loading EC2 OD sheet.

    # Phase 2: EC2 OD sheet — join to SP rates, then drop ~1.5 GB.
    ec2_sheet = stream_download_json(_ec2_od_rate_url(region, od_version))
    region_ec2 = _build_region_ec2_ratios(ec2_sheet, sp_ec2_rates)
    del ec2_sheet, sp_ec2_rates  # Release ~1.5 GB before next region.

    return region_ec2, lambda_rates_region, fargate_region


def build_cache(
    required_regions: set[str],
    primary_region: str,
    sp_versions: dict[str, str],
    od_versions: dict[str, str],
) -> Ratios:
    """Download SP + OD rate sheets and build the Ratios structure.

    See `_build_region_ratios` for the per-region memory strategy.
    """
    ec2: dict[str, dict[str, RatioEntry]] = {}
    lambda_rates: dict[str, RatioEntry] = {}
    fargate: dict[str, dict[str, RatioEntry]] = {}

    # Iterate in sorted order so that pricing-cache builds are deterministic
    # across runs (required_regions is a set). For Lambda, anchor the global
    # lambda_rates dict to primary_region — the data structure is flat
    # (not per-region), so we must pick one authoritative source rather than
    # rely on "last region wins" with set-order iteration.
    for region in sorted(required_regions):
        region_ec2, lambda_rates_region, fargate_region = _build_region_ratios(
            region, sp_versions[region], od_versions[region]
        )
        ec2[region] = region_ec2
        fargate[region] = fargate_region
        if region == primary_region and lambda_rates_region:
            lambda_rates = lambda_rates_region

    now = datetime.now(timezone.utc)
    ratios = Ratios(
        schema_version=CACHE_SCHEMA_VERSION,
        term="1 year No Upfront Compute Savings Plan",
        built_at=now,
        ec2=ec2,
        lambda_rates=lambda_rates,
        fargate=fargate,
        built_from_versions={"sp_by_region": sp_versions, "od_by_region": od_versions},
        primary_region=primary_region,
    )
    if not os.access(SHIPPED_DATA_DIR, os.W_OK):
        raise CacheError(
            code="shipped_data_not_writable",
            message=(
                f"Cannot write pricing cache: {SHIPPED_DATA_DIR} is not writable. "
                "This happens when the skill is installed read-only."
            ),
            context={"shipped_dir": str(SHIPPED_DATA_DIR)},
            user_fix_options=[
                "Ensure your user owns the skill directory",
                "Contact skill maintainer to refresh data/ and commit",
            ],
        )
    write_ratios_atomic(SHIPPED_RATIOS_PATH, ratios_to_json(ratios))

    # Write version metadata alongside
    meta = VersionMetadata(
        schema_version=CACHE_SCHEMA_VERSION,
        last_refreshed_at=now,
        aws_sp_optimizer_version=VERSION,
        regions={
            region: RegionVersionInfo(
                sp_version=sp_versions[region],
                od_version=od_versions[region],
                sp_refreshed_at=now,
                od_refreshed_at=now,
                ratio_entries_count=len(ec2.get(region, {})),
            )
            for region in required_regions
        },
        lambda_refreshed_at=now,
        fargate_refreshed_at=now,
    )
    write_version_metadata_atomic(SHIPPED_VERSION_PATH, version_metadata_to_json(meta))

    return ratios


def _resolve_offer_current_version(index_url: str, region: str) -> str:
    """Fetch an offer's region_index.json and return the current version for
    the given region. Used by Lambda/ECS flows that don't have their versions
    threaded through build_cache's arguments."""
    raw = stream_download_json(index_url)
    regions = raw.get("regions", {})
    if region not in regions:
        raise CacheError(
            code="region_not_in_offer_index",
            message=f"Region {region!r} missing from {index_url}",
            context={"index_url": index_url, "available_regions": sorted(regions.keys())[:10]},
        )
    version_url = regions[region].get("currentVersionUrl", "")
    return _extract_version_from_url(version_url)


def _build_lambda_rates(sp_sheet: dict, region: str) -> dict[str, RatioEntry]:
    """Lambda is globally priced — build once from the primary region's SP sheet.

    Lambda OD rates come from a separate offer file at AWSLambda, joined by
    usage_type (stored as 'group' in the product attributes). The OD version
    is fetched at call time because Lambda has its own release cadence
    independent of EC2 / SP.
    """
    term = _locate_1y_nu_compute_term(sp_sheet)
    result: dict[str, RatioEntry] = {}
    lambda_version = _resolve_offer_current_version(LAMBDA_OD_REGION_INDEX_URL, region)
    lambda_od_sheet = stream_download_json(_lambda_od_rate_url(region, lambda_version))
    for rate in term.get("rates", []):
        if rate.get("discountedServiceCode") != "AWSLambda":
            continue
        usage_type = rate.get("discountedUsageType", "")
        if not usage_type:
            continue
        try:
            sp_price = float(rate["discountedRate"]["price"])
        except (KeyError, ValueError):
            continue
        od_price = _extract_product_od_rate_by_key(lambda_od_sheet, "group", usage_type)
        if od_price is None or od_price <= 0:
            continue
        ratio = sp_price / od_price
        result[usage_type] = RatioEntry(
            sp_rate_usd=sp_price,
            od_rate_usd=od_price,
            ratio=ratio,
            discount_pct=1.0 - ratio,
        )
    return result


def _build_fargate_rates(sp_sheet: dict, region: str) -> dict[str, RatioEntry]:
    """Fargate is region-scoped. OD rates live in the AmazonECS offer, joined
    by usage_type (stored as 'usagetype' in product attributes)."""
    term = _locate_1y_nu_compute_term(sp_sheet)
    result: dict[str, RatioEntry] = {}
    ecs_version = _resolve_offer_current_version(ECS_OD_REGION_INDEX_URL, region)
    ecs_od_sheet = stream_download_json(_ecs_od_rate_url(region, ecs_version))
    for rate in term.get("rates", []):
        if rate.get("discountedServiceCode") not in ("AmazonECS", "AmazonEKS"):
            continue
        usage_type = rate.get("discountedUsageType", "")
        if not usage_type or "Fargate" not in usage_type:
            continue
        try:
            sp_price = float(rate["discountedRate"]["price"])
        except (KeyError, ValueError):
            continue
        od_price = _extract_product_od_rate_by_key(ecs_od_sheet, "usagetype", usage_type)
        if od_price is None or od_price <= 0:
            continue
        ratio = sp_price / od_price
        result[usage_type] = RatioEntry(
            sp_rate_usd=sp_price,
            od_rate_usd=od_price,
            ratio=ratio,
            discount_pct=1.0 - ratio,
        )
    return result


# ---------------------------------------------------------------------------
# Task 2.10: ensure_cache_fresh orchestration
# ---------------------------------------------------------------------------


def _build_cache_in_subprocess(
    required_regions: set[str],
    primary_region: str,
    sp_versions: dict[str, str],
    od_versions: dict[str, str],
) -> None:
    """Wrapper for ProcessPoolExecutor — must be a module-level function so the
    spawn context can pickle it.  Drops the Ratios return value; the parent
    re-reads ratios.json from disk after the subprocess exits."""
    build_cache(
        required_regions=required_regions,
        primary_region=primary_region,
        sp_versions=sp_versions,
        od_versions=od_versions,
    )


def _handle_skip_refresh(
    required_regions: set[str],
    primary_region: str,
) -> Ratios:
    """Handle the skip_refresh path: validate cache exists and covers regions."""
    cached_meta = load_version_metadata()
    ratios_exist = cached_meta is not None and SHIPPED_RATIOS_PATH.is_file()
    missing_regions = (
        required_regions - set(cached_meta.regions.keys())
        if cached_meta is not None
        else set(required_regions)
    )
    if not ratios_exist or missing_regions:
        raise CacheError(
            code="skip_refresh_but_no_cache",
            message=(
                "--skip-pricing-refresh was set but the required regions are "
                "not fully present in the existing cache."
            ),
            context={
                "ratios_exist": ratios_exist,
                "missing_regions": sorted(missing_regions) if missing_regions else [],
            },
            user_fix_options=[
                "Drop the --skip-pricing-refresh flag and rerun",
                "Run once online to warm the cache, then retry offline",
            ],
        )
    return load_and_validate_ratios(primary_region=primary_region)


def _fetch_current_versions(
    required_regions: set[str],
) -> tuple[dict, dict]:
    """Fetch SP and OD version strings from upstream. Raises CacheError on network failure."""
    try:
        sp_current_versions = fetch_sp_region_versions(required_regions)
        od_current_versions = fetch_od_region_versions(required_regions)
    except requests.exceptions.RequestException as e:
        raise CacheError(
            code="pricing_api_unreachable",
            message=str(e),
            context={"error_type": type(e).__name__},
        ) from e
    return sp_current_versions, od_current_versions


def _check_needs_rebuild(
    cached_meta,
    required_regions: set[str],
    sp_current_versions: dict,
    od_current_versions: dict,
    refresh_cache: bool,
) -> bool:
    """Return True if the cache must be rebuilt."""
    if refresh_cache or cached_meta is None:
        return True
    if cached_meta.schema_version != CACHE_SCHEMA_VERSION:
        return True
    if any(region not in cached_meta.regions for region in required_regions):
        return True
    if any(
        _is_version_stale(cached_meta.regions[r].sp_version, sp_current_versions[r])
        for r in required_regions
    ):
        return True
    if any(
        _is_version_stale(cached_meta.regions[r].od_version, od_current_versions[r])
        for r in required_regions
    ):
        return True
    return False


def _raise_needs_refresh(
    cached_meta,
    required_regions: set[str],
    sp_current_versions: dict,
    od_current_versions: dict,
) -> None:
    """Classify stale vs missing regions and raise NeedsCacheRefreshError."""
    stale_regions = []
    missing_regions = []
    for r in required_regions:
        if cached_meta is None or r not in cached_meta.regions:
            missing_regions.append(r)
            continue
        if (
            _is_version_stale(cached_meta.regions[r].sp_version, sp_current_versions[r])
            or _is_version_stale(cached_meta.regions[r].od_version, od_current_versions[r])
        ):
            stale_regions.append(r)
    code = "cache_missing_regions" if missing_regions else "cache_stale_over_1year"
    raise NeedsCacheRefreshError(
        code=code,
        message=(
            f"Pricing cache needs refresh. "
            f"Stale regions: {sorted(stale_regions)}. "
            f"Missing regions: {sorted(missing_regions)}."
        ),
        context={
            "stale_regions": sorted(stale_regions),
            "missing_regions": sorted(missing_regions),
            "cached_last_refreshed_at": (
                cached_meta.last_refreshed_at.isoformat() if cached_meta is not None else None
            ),
        },
    )


def _run_build_cache_subprocess(
    required_regions: set[str],
    primary_region: str,
    sp_current_versions: dict,
    od_current_versions: dict,
) -> None:
    """Run build_cache in a subprocess to release peak RSS when the child exits."""
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as executor:
        future = executor.submit(
            _build_cache_in_subprocess,
            required_regions,
            primary_region,
            sp_current_versions,
            od_current_versions,
        )
        try:
            future.result()  # re-raises CacheError from the subprocess
        except CacheError:
            raise
        except Exception as e:
            raise CacheError(
                code="pricing_cache_subprocess_failed",
                message=f"build_cache subprocess crashed: {type(e).__name__}: {e}",
                context={"error_type": type(e).__name__},
                user_fix_options=[
                    "Re-run with --refresh-cache to trigger a fresh rebuild",
                    "Check stderr for the subprocess traceback",
                ],
            ) from e


def ensure_cache_fresh(
    required_regions: set[str],
    primary_region: str,
    refresh_cache: bool = False,
    skip_refresh: bool = False,
) -> Ratios:
    """Return a Ratios covering required_regions, rebuilding if needed.

    primary_region MUST be in required_regions. See spec 7.5.
    """
    assert primary_region in required_regions, (
        f"primary_region {primary_region!r} must be in required_regions {sorted(required_regions)!r}"
    )

    if skip_refresh:
        return _handle_skip_refresh(required_regions, primary_region)

    sp_current_versions, od_current_versions = _fetch_current_versions(required_regions)

    cached_meta = load_version_metadata()
    needs_rebuild = _check_needs_rebuild(
        cached_meta, required_regions, sp_current_versions, od_current_versions, refresh_cache
    )

    if not needs_rebuild:
        return load_and_validate_ratios(primary_region=primary_region)

    if not refresh_cache:
        # Do NOT auto-rebuild. Raise so the orchestrator can present user actions.
        _raise_needs_refresh(cached_meta, required_regions, sp_current_versions, od_current_versions)

    _run_build_cache_subprocess(
        required_regions, primary_region, sp_current_versions, od_current_versions
    )

    return load_and_validate_ratios(primary_region=primary_region)
