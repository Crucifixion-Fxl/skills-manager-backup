#!/usr/bin/env python3
"""
Zendesk Help Center CLI helper.

Auth flow:
1) Assume AWS role
2) Read Zendesk tokens from AWS Secrets Manager
3) Build Zendesk API auth headers in-memory only
"""

import argparse
import json
import sys
import time
from base64 import b64encode
from pathlib import Path
from urllib.parse import urlparse

try:
    import boto3
except ImportError:
    print('{"error": "boto3 not installed. Run: pip install boto3"}', file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print('{"error": "requests not installed. Run: pip install requests"}', file=sys.stderr)
    sys.exit(1)


_cached_secrets = None

BRAND_CONFIG = {
    'vh': {'subdomain': 'addxai',  'email': 'xsun@addx.ai',  'token_key': 'VH_ZENDESK_API_TOKEN'},
    'sf': {'subdomain': 'safemo',  'email': 'cs@safemo.com',  'token_key': 'SF_ZENDESK_API_TOKEN'},
    'kb': {'subdomain': 'kiwibit', 'email': 'cs@kiwibit.com', 'token_key': 'KB_ZENDESK_API_TOKEN'},
}

AWS_ROLE_ARN = 'arn:aws:iam::002497567426:role/cs-tools-role'
AWS_SECRET_ID = 'prod/cstool/allpwd'
AWS_REGION = 'us-east-1'


def _load_secrets():
    global _cached_secrets
    if _cached_secrets is not None:
        return _cached_secrets

    sts = boto3.client('sts')
    assumed = sts.assume_role(RoleArn=AWS_ROLE_ARN, RoleSessionName='ZendeskHelper')
    creds = assumed['Credentials']

    sm = boto3.Session(
        aws_access_key_id=creds['AccessKeyId'],
        aws_secret_access_key=creds['SecretAccessKey'],
        aws_session_token=creds['SessionToken'],
    ).client('secretsmanager', region_name=AWS_REGION)

    resp = sm.get_secret_value(SecretId=AWS_SECRET_ID)
    _cached_secrets = json.loads(resp['SecretString'])
    return _cached_secrets


def _build_headers(brand, subdomain_override=None):
    cfg = BRAND_CONFIG[brand]
    token = _load_secrets()[cfg['token_key']]
    auth = b64encode(f"{cfg['email']}/token:{token}".encode()).decode()
    subdomain = subdomain_override or cfg['subdomain']
    base_url = f"https://{subdomain}.zendesk.com/api/v2"
    headers = {'Authorization': f'Basic {auth}', 'Content-Type': 'application/json'}
    return base_url, headers, subdomain


class ZendeskHC:
    def __init__(self, brand, max_retries=3, subdomain_override=None):
        self.base_url, self.headers, self.subdomain = _build_headers(brand, subdomain_override)
        self.brand = brand
        self.max_retries = max_retries

    def _req(self, method, path, data=None, params=None, help_center=True):
        url = f"{self.base_url}/help_center/{path}" if help_center else f"{self.base_url}/{path}"
        for _ in range(self.max_retries):
            resp = requests.request(method, url, headers=self.headers, json=data, params=params)
            if resp.status_code == 429:
                wait = int(resp.headers.get('Retry-After', 60))
                print(f"[rate-limit] waiting {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code == 404 and help_center:
                hint = self._subdomain_hint()
                raise requests.exceptions.HTTPError(
                    f"404 Not Found on {self.subdomain}.zendesk.com — "
                    f"resource may belong to a different subdomain. "
                    f"Use --subdomain to switch.{hint}",
                    response=resp,
                )
            resp.raise_for_status()
            return resp.json() if resp.status_code != 204 else {'success': True}
        raise RuntimeError('max retries exceeded')

    def _subdomain_hint(self):
        try:
            brands_url = f"{self.base_url}/brands.json"
            resp = requests.get(brands_url, headers=self.headers)
            if resp.status_code != 200:
                return ''
            brands = resp.json().get('brands', [])
            subs = []
            for b in brands:
                name = b.get('name', '?')
                sd = b.get('subdomain', '?')
                bid = b.get('id', '?')
                hc = 'HC enabled' if b.get('has_help_center') else 'no HC'
                subs.append(f"  {sd} (name={name}, brand_id={bid}, {hc})")
            if subs:
                return '\nAvailable subdomains:\n' + '\n'.join(subs)
        except Exception:
            pass
        return ''

    def _paginate(self, path, collection_key, params=None, help_center=True):
        all_items = []
        page_params = dict(params or {})
        page_params.setdefault('per_page', 100)
        while True:
            result = self._req('GET', path, params=page_params, help_center=help_center)
            items = result.get(collection_key, [])
            all_items.extend(items)
            next_page = result.get('next_page')
            if not next_page:
                break
            page_params['page'] = page_params.get('page', 1) + 1
        return {collection_key: all_items, 'count': len(all_items)}

    def _plural(self, item_type):
        return 'categories' if item_type == 'category' else f'{item_type}s'

    # ── Category ──

    def list_categories(self):
        return self._paginate('categories.json', 'categories')

    def get_category(self, category_id):
        return self._req('GET', f'categories/{category_id}.json')

    def create_category(self, name, locale='en-us', description='', helpcenter_brand_id=None):
        payload = {'name': name, 'locale': locale, 'description': description}
        if helpcenter_brand_id is not None:
            payload['brand_id'] = helpcenter_brand_id
        return self._req('POST', 'categories.json', {'category': payload})

    def update_category(self, category_id, **fields):
        return self._req('PUT', f'categories/{category_id}.json', {'category': fields})

    def delete_category(self, category_id):
        return self._req('DELETE', f'categories/{category_id}.json')

    # ── Section ──

    def list_sections(self, category_id):
        return self._paginate(f'categories/{category_id}/sections.json', 'sections')

    def get_section(self, section_id):
        return self._req('GET', f'sections/{section_id}.json')

    def create_section(self, category_id, name, locale='en-us', description=''):
        return self._req('POST', f'categories/{category_id}/sections.json', {
            'section': {'name': name, 'locale': locale, 'description': description}
        })

    def update_section(self, section_id, **fields):
        return self._req('PUT', f'sections/{section_id}.json', {'section': fields})

    def delete_section(self, section_id):
        return self._req('DELETE', f'sections/{section_id}.json')

    # ── Article ──

    def list_articles(self, section_id):
        return self._paginate(f'sections/{section_id}/articles.json', 'articles')

    def get_article(self, article_id):
        return self._req('GET', f'articles/{article_id}.json')

    def create_article(self, section_id, title, body, locale='en-us', draft=True):
        return self._req('POST', f'sections/{section_id}/articles.json', {
            'article': {'title': title, 'body': body, 'locale': locale, 'draft': draft}
        })

    def update_article(self, article_id, **fields):
        return self._req('PUT', f'articles/{article_id}.json', {'article': fields})

    def delete_article(self, article_id):
        return self._req('DELETE', f'articles/{article_id}.json')

    def search_articles(self, query, category_id=None, section_id=None, locale=None):
        params = {'query': query}
        if category_id:
            params['category'] = category_id
        if section_id:
            params['section'] = section_id
        if locale:
            params['locale'] = locale
        return self._paginate('articles/search.json', 'results', params=params)

    # ── Translation ──

    def get_translations(self, item_type, item_id):
        return self._req('GET', f'{self._plural(item_type)}/{item_id}/translations.json')

    def list_missing_translations(self, item_type, item_id):
        return self._req('GET', f'{self._plural(item_type)}/{item_id}/translations/missing.json')

    def create_translation(self, item_type, item_id, locale, title, body):
        return self._req('POST', f'{self._plural(item_type)}/{item_id}/translations.json', {
            'translation': {'locale': locale, 'title': title, 'body': body}
        })

    def update_translation(self, item_type, item_id, locale, **fields):
        fields['locale'] = locale
        return self._req('PUT', f'{self._plural(item_type)}/{item_id}/translations/{locale}.json', {
            'translation': fields
        })

    def delete_translation(self, item_type, item_id, locale):
        return self._req('DELETE', f'{self._plural(item_type)}/{item_id}/translations/{locale}.json')

    # ── Attachment ──

    def get_attachments(self, article_id):
        return self._req('GET', f'articles/{article_id}/attachments.json')

    # ── Brand & Locale ──

    def list_brands(self):
        return self._req('GET', 'brands.json', help_center=False)

    def get_brand(self, brand_id):
        return self._req('GET', f'brands/{brand_id}.json', help_center=False)

    def list_locales(self):
        return self._req('GET', 'locales.json', help_center=False)


def _read_body(args):
    if getattr(args, 'body_file', None):
        return Path(args.body_file).read_text(encoding='utf-8')
    body = getattr(args, 'body', None)
    if body == '-':
        return sys.stdin.read()
    return body


def _add_body_args(parser, required=False):
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument('--body', help='HTML content (use "-" to read from stdin)')
    group.add_argument('--body-file', help='Read HTML content from file')


def _output(result):
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _extract_host(value):
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if '://' not in raw:
        raw = f'https://{raw}'
    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path
    return host.lower() if host else None


def _brand_hosts(brand):
    hosts = set()
    for key in ('brand_url', 'host_mapping', 'help_center_url'):
        value = brand.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                for sub_key in ('brand_url', 'host_mapping', 'url', 'host', 'domain'):
                    host = _extract_host(item.get(sub_key))
                    if host:
                        hosts.add(host)
            else:
                host = _extract_host(item)
                if host:
                    hosts.add(host)
    return hosts


def _verify_category_brand(client, category, helpcenter_brand_id):
    if not isinstance(category, dict):
        raise RuntimeError('create-category returned invalid response: missing category object')

    actual_brand_id = category.get('brand_id')
    if actual_brand_id is not None and int(actual_brand_id) != int(helpcenter_brand_id):
        raise RuntimeError(
            f'brand mismatch: expected brand_id={helpcenter_brand_id}, got brand_id={actual_brand_id}'
        )

    actual_host = _extract_host(category.get('html_url'))
    if not actual_host:
        return

    brand_resp = client.get_brand(helpcenter_brand_id)
    brand = brand_resp.get('brand', {}) if isinstance(brand_resp, dict) else {}
    expected_hosts = _brand_hosts(brand)
    if not expected_hosts:
        return

    if actual_host not in expected_hosts:
        raise RuntimeError(
            f'brand host mismatch: expected one of {sorted(expected_hosts)}, got {actual_host}. '
            'Please verify --helpcenter-brand-id before retrying.'
        )


def _error(exc):
    err = {'error': str(exc)}
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        err['status_code'] = exc.response.status_code
        try:
            err['detail'] = exc.response.json()
        except Exception:
            err['detail'] = exc.response.text
    print(json.dumps(err, ensure_ascii=False, indent=2), file=sys.stderr)


def _dispatch(client, args):
    cmd = args.cmd

    # ── Category ──
    if cmd == 'list-categories':
        return client.list_categories()
    if cmd == 'get-category':
        return client.get_category(args.cid)
    if cmd == 'create-category':
        result = client.create_category(
            args.name, args.locale, args.description, args.helpcenter_brand_id
        )
        _verify_category_brand(client, result.get('category'), args.helpcenter_brand_id)
        return result
    if cmd == 'update-category':
        fields = {}
        if args.name:
            fields['name'] = args.name
        if args.description is not None:
            fields['description'] = args.description
        return client.update_category(args.cid, **fields)
    if cmd == 'delete-category':
        return client.delete_category(args.cid)

    # ── Section ──
    if cmd == 'list-sections':
        return client.list_sections(args.category_id)
    if cmd == 'get-section':
        return client.get_section(args.sid)
    if cmd == 'create-section':
        return client.create_section(args.category_id, args.name, args.locale, args.description)
    if cmd == 'update-section':
        fields = {}
        if args.name:
            fields['name'] = args.name
        if args.description is not None:
            fields['description'] = args.description
        return client.update_section(args.sid, **fields)
    if cmd == 'delete-section':
        return client.delete_section(args.sid)

    # ── Article ──
    if cmd == 'list-articles':
        return client.list_articles(args.section_id)
    if cmd == 'get-article':
        return client.get_article(args.aid)
    if cmd == 'create-article':
        return client.create_article(
            args.section_id, args.title, _read_body(args), args.locale, args.draft.lower() == 'true'
        )
    if cmd == 'update-article':
        fields = {}
        if args.title:
            fields['title'] = args.title
        body = _read_body(args)
        if body:
            fields['body'] = body
        if args.draft is not None:
            fields['draft'] = args.draft.lower() == 'true'
        return client.update_article(args.aid, **fields)
    if cmd == 'delete-article':
        return client.delete_article(args.aid)
    if cmd == 'search-articles':
        return client.search_articles(
            args.query,
            category_id=getattr(args, 'category_id', None),
            section_id=getattr(args, 'section_id', None),
            locale=getattr(args, 'locale', None),
        )

    # ── Translation ──
    if cmd == 'get-translations':
        return client.get_translations(args.item_type, args.item_id)
    if cmd == 'list-missing-translations':
        return client.list_missing_translations(args.item_type, args.item_id)
    if cmd == 'create-translation':
        return client.create_translation(
            args.item_type, args.item_id, args.locale, args.title, _read_body(args)
        )
    if cmd == 'update-translation':
        fields = {}
        if args.title:
            fields['title'] = args.title
        body = _read_body(args)
        if body:
            fields['body'] = body
        return client.update_translation(args.item_type, args.item_id, args.locale, **fields)
    if cmd == 'delete-translation':
        return client.delete_translation(args.item_type, args.item_id, args.locale)

    # ── Attachment ──
    if cmd == 'get-attachments':
        return client.get_attachments(args.aid)

    # ── Brand & Locale ──
    if cmd == 'list-brands':
        return client.list_brands()
    if cmd == 'list-locales':
        return client.list_locales()

    raise ValueError(f'unknown command: {cmd}')


def main():
    parser = argparse.ArgumentParser(description='Zendesk Help Center CLI (AWS SM auth)')
    parser.add_argument('--brand', choices=list(BRAND_CONFIG), default='vh',
                        help='Auth credential key (vh/sf/kb)')
    parser.add_argument('--subdomain',
                        help='Override the subdomain (e.g. vicohome, addxai). '
                             'Uses brand default if omitted.')
    subparsers = parser.add_subparsers(dest='cmd', required=True)

    subparsers.add_parser('check-auth', help='Validate AWS credential chain')

    # ── Category ──
    subparsers.add_parser('list-categories', help='List all categories')

    p = subparsers.add_parser('get-category', help='Get category by ID')
    p.add_argument('--id', required=True, type=int, dest='cid')

    p = subparsers.add_parser('create-category', help='Create category')
    p.add_argument('--name', required=True)
    p.add_argument('--locale', default='en-us')
    p.add_argument('--description', default='')
    p.add_argument('--helpcenter-brand-id', required=True, type=int,
                   help='Target Help Center brand_id (required in multi-brand)')

    p = subparsers.add_parser('update-category', help='Update category')
    p.add_argument('--id', required=True, type=int, dest='cid')
    p.add_argument('--name')
    p.add_argument('--description')

    p = subparsers.add_parser('delete-category', help='Delete category by ID')
    p.add_argument('--id', required=True, type=int, dest='cid')

    # ── Section ──
    p = subparsers.add_parser('list-sections', help='List sections under a category')
    p.add_argument('--category-id', required=True, type=int)

    p = subparsers.add_parser('get-section', help='Get section by ID')
    p.add_argument('--id', required=True, type=int, dest='sid')

    p = subparsers.add_parser('create-section', help='Create section')
    p.add_argument('--category-id', required=True, type=int)
    p.add_argument('--name', required=True)
    p.add_argument('--locale', default='en-us')
    p.add_argument('--description', default='')

    p = subparsers.add_parser('update-section', help='Update section')
    p.add_argument('--id', required=True, type=int, dest='sid')
    p.add_argument('--name')
    p.add_argument('--description')

    p = subparsers.add_parser('delete-section', help='Delete section by ID')
    p.add_argument('--id', required=True, type=int, dest='sid')

    # ── Article ──
    p = subparsers.add_parser('list-articles', help='List articles under a section')
    p.add_argument('--section-id', required=True, type=int)

    p = subparsers.add_parser('get-article', help='Get article by ID')
    p.add_argument('--id', required=True, type=int, dest='aid')

    p = subparsers.add_parser('create-article', help='Create article')
    p.add_argument('--section-id', required=True, type=int)
    p.add_argument('--title', required=True)
    _add_body_args(p, required=True)
    p.add_argument('--locale', default='en-us')
    p.add_argument('--draft', default='true', help='true/false (default: true)')

    p = subparsers.add_parser('update-article', help='Update article')
    p.add_argument('--id', required=True, type=int, dest='aid')
    p.add_argument('--title')
    _add_body_args(p)
    p.add_argument('--draft', help='true/false')

    p = subparsers.add_parser('delete-article', help='Delete article by ID')
    p.add_argument('--id', required=True, type=int, dest='aid')

    p = subparsers.add_parser('search-articles', help='Search articles by keyword')
    p.add_argument('--query', required=True, help='Search keyword')
    p.add_argument('--category-id', type=int)
    p.add_argument('--section-id', type=int)
    p.add_argument('--locale')

    # ── Translation ──
    p = subparsers.add_parser('get-translations', help='Get translation list')
    p.add_argument('--item-type', choices=['article', 'section', 'category'], default='article')
    p.add_argument('--item-id', required=True, type=int)

    p = subparsers.add_parser('list-missing-translations', help='List missing translations for an item')
    p.add_argument('--item-type', choices=['article', 'section', 'category'], default='article')
    p.add_argument('--item-id', required=True, type=int)

    p = subparsers.add_parser('create-translation', help='Create translation')
    p.add_argument('--item-type', choices=['article', 'section', 'category'], default='article')
    p.add_argument('--item-id', required=True, type=int)
    p.add_argument('--locale', required=True)
    p.add_argument('--title', required=True)
    _add_body_args(p, required=True)

    p = subparsers.add_parser('update-translation', help='Update translation')
    p.add_argument('--item-type', choices=['article', 'section', 'category'], default='article')
    p.add_argument('--item-id', required=True, type=int)
    p.add_argument('--locale', required=True)
    p.add_argument('--title')
    _add_body_args(p)

    p = subparsers.add_parser('delete-translation', help='Delete translation')
    p.add_argument('--item-type', choices=['article', 'section', 'category'], default='article')
    p.add_argument('--item-id', required=True, type=int)
    p.add_argument('--locale', required=True)

    # ── Attachment ──
    p = subparsers.add_parser('get-attachments', help='Get article attachments')
    p.add_argument('--id', required=True, type=int, dest='aid')

    # ── Brand & Locale ──
    subparsers.add_parser('list-brands', help='List Help Center brands (includes brand_id)')
    subparsers.add_parser('list-locales', help='List all locales enabled on the account')

    args = parser.parse_args()

    try:
        if args.cmd == 'check-auth':
            _load_secrets()
            _output({'status': 'ok', 'brands': list(BRAND_CONFIG.keys())})
            return

        client = ZendeskHC(args.brand, subdomain_override=args.subdomain)
        result = _dispatch(client, args)
        _output(result)
    except Exception as exc:
        _error(exc)
        sys.exit(1)


if __name__ == '__main__':
    main()
