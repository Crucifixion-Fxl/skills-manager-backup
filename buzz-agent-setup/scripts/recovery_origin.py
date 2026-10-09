"""One canonical ASCII transport origin for history and exact AttemptIDs."""
import ipaddress
import re
import urllib.parse


def canonical_origin(origin):
    if not isinstance(origin, str) or any(ord(c) <= 32 or ord(c) >= 127 or c == "%" for c in origin):
        raise ValueError("recovery relay requires an explicit ASCII origin")
    parts = urllib.parse.urlsplit(origin)
    scheme = {"wss": "https", "ws": "http"}.get(parts.scheme, parts.scheme)
    host, port = parts.hostname, parts.port
    if (scheme not in ("http", "https") or not host or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ("", "/")
            or "?" in origin or "#" in origin or "@" in parts.netloc):
        raise ValueError("unsafe recovery relay origin")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9._-]+", host) or re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", host.rstrip(".").rsplit(".", 1)[-1]):
            raise ValueError("recovery relay requires an explicit DNS name or IP address")
        local = host == "localhost"
    else:
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        local = address.is_loopback
    if scheme == "http" and not local:
        raise ValueError("plaintext recovery relay must be loopback")
    suffix = "" if port is None or port == {"https": 443, "http": 80}[scheme] else f":{port}"
    return f"{scheme}://{host}{suffix}"
