"""
Tests for Oxylabs proxy connectivity.

Run:  pytest tests/test_proxy.py -v
"""

import asyncio

import pytest

from url_collector.config import ProxyConfig


# ------------------------------------------------------------------
# Sync proxy tests (using requests)
# ------------------------------------------------------------------

def _get_ip_via_requests(proxy_url: str) -> str:
    import requests
    resp = requests.get(
        "https://httpbin.org/ip",
        proxies={"https": proxy_url, "http": proxy_url},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["origin"]


def test_proxy_credentials_loaded():
    """Verify OXYLABS_* env vars were loaded from .env."""
    proxy = ProxyConfig()
    assert proxy.username, "OXYLABS_USERNAME is empty — check .env file"
    assert proxy.password, "OXYLABS_PASSWORD is empty — check .env file"
    assert proxy.country, "OXYLABS_COUNTRY is empty — check .env file"


def test_proxy_url_format():
    """Proxy URL should match Oxylabs customer format."""
    proxy = ProxyConfig()
    url = proxy.get_url("test-session-001")
    assert "customer-" in url
    assert "pr.oxylabs.io:7777" in url
    assert proxy.country in url
    assert "test-session-001" in url


@pytest.mark.integration
def test_proxy_connectivity_httpbin():
    """
    Live test: verify proxy routes traffic through Oxylabs.
    Skipped automatically if no credentials are set.
    """
    proxy = ProxyConfig()
    if not proxy.enabled:
        pytest.skip("No Oxylabs credentials configured")

    proxy_url = proxy.get_url("pytest-conn-test")
    ip = _get_ip_via_requests(proxy_url)
    assert ip, "Got empty IP from httpbin"
    print(f"\nProxy exit IP: {ip}")


@pytest.mark.integration
def test_proxy_session_rotation_changes_ip():
    """
    Verify that different session IDs produce different exit IPs.
    (Oxylabs assigns a fresh IP per unique session ID.)
    """
    proxy = ProxyConfig()
    if not proxy.enabled:
        pytest.skip("No Oxylabs credentials configured")

    ip1 = _get_ip_via_requests(proxy.get_url("sess-aaa"))
    ip2 = _get_ip_via_requests(proxy.get_url("sess-bbb"))

    print(f"\nSession A IP: {ip1}")
    print(f"Session B IP: {ip2}")
    # IPs should differ — Oxylabs assigns new IP per session ID
    assert ip1 != ip2, (
        "Session A and B have the same exit IP — session rotation may not be working"
    )


# ------------------------------------------------------------------
# Async proxy test (via curl_cffi fetcher)
# ------------------------------------------------------------------

@pytest.mark.integration
def test_proxy_via_curl_cffi_fetcher(fetcher, proxy_config):
    """
    Verify curl_cffi fetcher can make a request through Oxylabs proxy.
    """
    if not proxy_config.enabled:
        pytest.skip("No Oxylabs credentials configured")

    async def _run():
        html = await fetcher.fetch("https://httpbin.org/ip")
        return html

    html = asyncio.get_event_loop().run_until_complete(_run())
    assert html is not None
    assert '"origin"' in html
    print(f"\ncurl_cffi proxy response snippet: {html[:200]}")
