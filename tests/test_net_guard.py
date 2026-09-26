"""SSRF / local-file URL guard (offline — IP literals need no DNS)."""

import ipaddress

import pytest

from oli import net_guard
from oli.net_guard import UnsafeURLError, validate_url


def test_allows_public_ip_literal():
    # A public IP literal resolves to itself; no DNS, no block.
    assert validate_url("http://93.184.216.34/x") == "http://93.184.216.34/x"


def test_defaults_bare_host_to_https(monkeypatch):
    monkeypatch.setattr(net_guard, "_resolve", lambda host: [ipaddress.ip_address("8.8.8.8")])
    assert validate_url("example.com/path") == "https://example.com/path"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/html,<script>",
        "javascript:alert(1)",
        "chrome://settings",
        "ftp://example.com/x",
    ],
)
def test_rejects_non_http_schemes(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private
        "192.168.1.1",  # private
        "172.16.0.1",  # private
        "169.254.169.254",  # link-local / cloud metadata
        "0.0.0.0",  # unspecified
        "::1",  # ipv6 loopback
        "::ffff:169.254.169.254",  # ipv4-mapped metadata
    ],
)
def test_rejects_internal_ip_literals(ip):
    with pytest.raises(UnsafeURLError):
        validate_url(f"http://{ip}/")


def test_rejects_hostname_resolving_to_private(monkeypatch):
    monkeypatch.setattr(net_guard, "_resolve", lambda host: [ipaddress.ip_address("10.1.2.3")])
    with pytest.raises(UnsafeURLError):
        validate_url("http://intranet.example/")


def test_rejects_unresolvable_host_fail_closed():
    # A host that can't be resolved is refused, not allowed through.
    with pytest.raises(UnsafeURLError):
        validate_url("http://nonexistent.invalid/")
