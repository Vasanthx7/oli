"""SSRF / local-file guards for user- and agent-supplied URLs.

Any URL that Oli fetches or navigates on behalf of a caller (the ``web_fetch``
tool, the live-browser navigate endpoint, the Fara ``visit_url`` action) can be
steered by untrusted input — a prompt-injected web page, or, since the app faces
the internet, an anonymous request. Left unguarded, such a URL can reach the
cloud metadata service (``169.254.169.254``), internal services, or the local
filesystem (``file://``).

``validate_url`` is the single choke point: it accepts only http(s) and rejects
hosts that resolve to a private, loopback, link-local, reserved, or multicast
address. Every entry point routes user/agent URLs through it.

Note on DNS rebinding: we resolve and validate the host, but the HTTP client
re-resolves at connect time, so a host that flips its record between the two
lookups could still slip through. Full mitigation would pin the validated IP
into the connection; for this single-user app the resolve-and-check guard plus
per-redirect re-validation covers the realistic prompt-injection SSRF path.
"""

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = ("http", "https")


class UnsafeURLError(ValueError):
    """A URL uses a disallowed scheme or resolves to a private/internal host."""


def normalize_url(url: str) -> str:
    """Trim whitespace and default a bare host to https://. Does not validate."""
    url = url.strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


def validate_url(url: str) -> str:
    """Return a normalized, safe-to-fetch http(s) URL, or raise ``UnsafeURLError``.

    Blocks non-http(s) schemes (``file:``, ``data:``, ``javascript:``, ...) and
    any host that resolves to a private, loopback, link-local (incl. the cloud
    metadata IP), reserved, multicast, or unspecified address.
    """
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"unsupported URL scheme: {parsed.scheme or '(none)'!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    for ip in _resolve(host):
        if _is_blocked(ip):
            raise UnsafeURLError(f"host {host!r} resolves to a disallowed address ({ip})")
    return normalized


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``host`` to every IP it maps to (a literal IP resolves to itself)."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"could not resolve host {host!r}") from e
    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        addr = str(info[4][0]).split("%")[0]  # strip any IPv6 zone id (fe80::1%eth0)
        try:
            out.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    if not out:
        raise UnsafeURLError(f"could not resolve host {host!r}")
    return out


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address class an outbound fetch must never reach."""
    # Unwrap IPv4-mapped IPv6 (::ffff:169.254.169.254) so the v4 checks apply.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private  # RFC1918 + ULA fc00::/7 (incl. AWS IMDSv6 fd00:ec2::254)
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16 (incl. metadata 169.254.169.254), fe80::/10
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
