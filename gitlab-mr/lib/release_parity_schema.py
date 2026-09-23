"""发布契约支持的字段集合。"""

CONTENT_CHECK_KEYS = {
    "ref",
    "path",
    "contains",
    "not_contains",
    "sha256",
    "absent",
    "object_type",
    "mode",
}
ALLOW_RULE_KEYS = {
    "paths",
    "reason",
    "owner",
    "temporary",
    "expires",
    "required_content",
}
