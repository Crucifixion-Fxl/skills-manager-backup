"""Executable calibration source; not production code or an analysis classifier."""


def effective_limit(primary, fallback, optional):
    limit = None
    if primary is not None:
        limit = int(primary)
    elif fallback is not None:
        limit = int(fallback)
    if optional:
        return limit
    if limit is None:
        raise ValueError("limit required")
    return limit


def process(decode_ok, route_ok):
    if not decode_ok:
        return "decode_failed"
    if not route_ok:
        return "route_failed"
    return "completed"
