#!/usr/bin/env python3
"""Fetch bird encyclopedia content + rarity from KB (naturehood) prod-us.

Resolves common_name -> scientific_name by paginating /api/species/nearby
(no region, ORDER BY rarity ASC, common_name), then fetches
/api/species/content per requested locale.

Usage:
  python3 fetch_bird.py --name "Rose-breasted Grosbeak"
  python3 fetch_bird.py --name "Rose-breasted Grosbeak" --locales en,zh,ja
  python3 fetch_bird.py --scientific "Pheucticus ludovicianus" --locales en
  python3 fetch_bird.py --name "..." --output-dir ./out

Output: <scientific_name>.json with parsed sections per locale + rarity.
"""

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


AUTH_BASE = "https://api-us.vicohome.io"
NH_BASE = "https://naturehood-prod-us.kiwibit.com"
APP_BODY = {
    "tenantId": "vicoo",
    "appName": "naturehood",
    "appType": "Flutter",
    "bundleId": "com.smartaddx.vicohome.nature",
    "appVersion": "0.1.0",
}

RARITY_LABELS = {
    1: "Common",
    2: "Uncommon",
    3: "Rare",
    4: "Very Rare",
    5: "Critically Endangered / Recovering",
    6: "Possibly Extinct",
}


def _make_ssl_context():
    # Default: strict TLS verification. Opt-out for users whose local Python
    # ships without a CA bundle (notably python.org installer on macOS that
    # hasn't run "Install Certificates.command"). The opt-out is explicit so
    # the security posture is visible to whoever runs the script.
    if os.environ.get("KB_VERIFY_SSL") == "skip":
        ctx = ssl.create_default_context()  # NOSONAR S5527,S4830 — explicit opt-out, see docstring
        ctx.check_hostname = False  # NOSONAR
        ctx.verify_mode = ssl.CERT_NONE  # NOSONAR
        return ctx
    return ssl.create_default_context()


_SSL_CTX = _make_ssl_context()


def http_json(method, url, headers=None, body=None, timeout=30):
    data = None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            body_text = "(no body)"
        raise RuntimeError(
            f"HTTP {e.code} {e.reason} from {url}\n  body: {body_text[:500]}"
        ) from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        # Check TLS first: a TLS error can happen on any host (including the
        # office-IP-gated one), so if we matched on hostname first we'd
        # mis-attribute genuine TLS misconfigs to "wrong network".
        if isinstance(reason, ssl.SSLError) or "SSL" in str(reason).upper():
            hint = (
                "\n  TLS verification failed. Two ways to fix:\n"
                "    (a) Recommended — fix your local CA bundle. If you used python.org's"
                " macOS installer, run:\n"
                "          /Applications/Python\\ 3.x/Install\\ Certificates.command\n"
                "    (b) Quick opt-out for this script only (script targets internal hosts;"
                " trade-off is no MITM protection on the SSL handshake):\n"
                "          export KB_VERIFY_SSL=skip && <rerun command>"
            )
        elif "naturehood-prod-us" in url:
            hint = " (office-IP-restricted; need office network or VPN)"
        else:
            hint = ""
        raise RuntimeError(f"urlopen failed for {url}: {reason}{hint}") from e


def login(email, password):
    encry = http_json(
        "POST",
        f"{AUTH_BASE}/user/encry",
        body={"email": email, "app": APP_BODY},
    )
    need_encrypt = (encry.get("data") or {}).get("encry") == 1
    final_password = password
    auth_version = 0
    if need_encrypt:
        final_password = hashlib.sha256(password.encode("utf-8")).hexdigest().lower()
        auth_version = 1
    login_resp = http_json(
        "POST",
        f"{AUTH_BASE}/account/login/",
        body={
            "email": email,
            "password": final_password,
            "loginType": 0,
            "authVersion": auth_version,
            "app": APP_BODY,
        },
    )
    if login_resp.get("result") != 0:
        raise RuntimeError(f"Login failed: {login_resp.get('msg')}")
    token = ((login_resp.get("data") or {}).get("token") or {}).get("token")
    if not token:
        raise RuntimeError("Login returned no token")
    return token  # already includes "Bearer " prefix


def _match_in_page(species, needle):
    """First species in this page whose common_name or scientific_name
    contains needle (case-insensitive). Exact match implies substring match,
    so a single `in` check is sufficient."""
    for s in species:
        cn = (s.get("common_name") or "").lower()
        sn = (s.get("scientific_name") or "").lower()
        if needle in cn or needle in sn:
            return s
    return None


PAGINATION_CEILING = 20000


def find_species(token, name, page_size=200):
    """Scan /api/species/nearby for a case-insensitive substring match on
    common_name or scientific_name. Returns the first hit (lowest rarity,
    earliest alphabetically), or None.

    Stops at PAGINATION_CEILING records — well above the current species
    count, but worth guarding so a buggy API loop can't run forever. Prints
    a clear stderr warning if the ceiling is hit without a match, so callers
    can distinguish "exhausted the dataset" from "we gave up early".
    """
    needle = name.strip().lower()
    headers = {"Authorization": token}
    last_offset = 0
    for offset in range(0, PAGINATION_CEILING + 1, page_size):
        last_offset = offset
        url = (
            f"{NH_BASE}/api/species/nearby?"
            f"limit={page_size}&offset={offset}&locale=en"
        )
        species = http_json("GET", url, headers=headers).get("species") or []
        if not species:
            return None
        hit = _match_in_page(species, needle)
        if hit:
            return hit
    print(
        f"WARN: scanned {last_offset + page_size} records without matching '{name}'. "
        "Pagination ceiling reached; result may be incomplete if the dataset grew. "
        "Try a more specific name or use --scientific.",
        file=sys.stderr,
    )
    return None


def lookup_rarity_by_scientific(token, scientific_name, page_size=200):
    """If user gave scientific_name directly, still need rarity from nearby."""
    return find_species(token, scientific_name, page_size=page_size)


def fetch_content(token, scientific_name, locale):
    headers = {"Authorization": token}
    q = urllib.parse.urlencode({"name": scientific_name, "locale": locale})
    url = f"{NH_BASE}/api/species/content?{q}"
    return http_json("GET", url, headers=headers)


def safe_filename(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def _resolve_species(token, args):
    """Return the matched species dict, or sys.exit(2) with a user-facing error."""
    if args.scientific:
        match = lookup_rarity_by_scientific(token, args.scientific)
        ok = match and match.get("scientific_name", "").lower() == args.scientific.lower()
        if not ok:
            print(f"ERROR: scientific_name '{args.scientific}' not found in species_reference.",
                  file=sys.stderr)
            sys.exit(2)
        return match
    match = find_species(token, args.name)
    if not match:
        print(f"ERROR: common name '{args.name}' not found. Try a more exact name "
              f"or use --scientific.", file=sys.stderr)
        sys.exit(2)
    return match


def _fetch_locale_sections(token, scientific, locale, log):
    """Fetch + parse one locale. Returns sections dict, or None if the locale
    isn't available (backend fell back to a different language)."""
    try:
        resp = fetch_content(token, scientific, locale)
    except Exception as e:
        log(f"  WARN: {locale} fetch failed: {e}")
        return None
    if locale != "en" and resp.get("locale") != locale:
        log(f"  NOTE: locale '{locale}' has no content; "
            f"backend fell back to '{resp.get('locale')}'. Skipping export for {locale}.")
        return None
    sections_raw = resp.get("sections") or "{}"
    try:
        return json.loads(sections_raw)
    except Exception:
        return {"_raw": sections_raw}


def _print_summary(fpath, match, out, missing_locales):
    print(f"Wrote {fpath}")
    print(f"  Common name:  {match['common_name']}")
    print(f"  Scientific:   {match['scientific_name']}")
    print(f"  Rarity:       {match.get('rarity', 0)} ({out['rarity_label']})")
    print(f"  Conservation: {out['conservation_status']}")
    exported = list(out["locales"].keys())
    print(f"  Locales exported: {', '.join(exported) if exported else '(none)'}")
    if missing_locales:
        print(f"  Locales NOT available: {', '.join(missing_locales)}",
              file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", help="Common name, e.g. 'Rose-breasted Grosbeak'")
    g.add_argument("--scientific", help="Scientific name, e.g. 'Pheucticus ludovicianus'")
    ap.add_argument("--locales", default="en",
                    help="Comma-separated locale codes. Default: en")
    ap.add_argument("--output-dir", default=".",
                    help="Where to write the JSON file. Default: cwd")
    ap.add_argument("--email", default=os.environ.get("KB_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("KB_PASSWORD"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.email or not args.password:
        print(
            "ERROR: missing KB credentials.\n"
            "  Provide either:\n"
            "    --email <addr> --password <pw>\n"
            "  or environment variables:\n"
            "    export KB_EMAIL=... KB_PASSWORD=...\n"
            "  See the 'Obtaining test credentials' section in SKILL.md for how to get an account.",
            file=sys.stderr,
        )
        sys.exit(2)

    def log(*a):
        if not args.quiet:
            print(*a, file=sys.stderr)

    log(f"Logging in as {args.email} ...")
    token = login(args.email, args.password)
    log("OK, got JWT.")

    log("Resolving species ...")
    match = _resolve_species(token, args)

    scientific = match["scientific_name"]
    rarity = match.get("rarity", 0)
    rarity_label = RARITY_LABELS.get(rarity, f"unknown({rarity})")
    log(f"Matched: {match['common_name']} ({scientific})  rarity={rarity} [{rarity_label}]")

    out = {
        "scientific_name": scientific,
        "common_name_en": match["common_name"],
        "rarity": rarity,
        "rarity_label": rarity_label,
        "conservation_status": match.get("conservation_status"),
        "image_url": match.get("image_url"),
        "silhouette_url": match.get("silhouette_url"),
        "species_id": match.get("id"),
        "locales": {},
    }
    missing_locales = []
    for locale in (l.strip() for l in args.locales.split(",") if l.strip()):
        log(f"Fetching {locale} content ...")
        sections = _fetch_locale_sections(token, scientific, locale, log)
        if sections is None:
            missing_locales.append(locale)
        else:
            out["locales"][locale] = sections

    os.makedirs(args.output_dir, exist_ok=True)
    fpath = os.path.join(args.output_dir, f"{safe_filename(scientific)}.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    _print_summary(fpath, match, out, missing_locales)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
