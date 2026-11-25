"""Security utilities: SSRF protection, IP filtering, safe HTTP client, and constant-time auth."""

import ipaddress
import secrets
import socket
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.core.exceptions import SSRFSecurityViolation

# Disallowed private, loopback, link-local, and cloud metadata network ranges
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / AWS / GCP metadata (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 Unique Local
    ipaddress.ip_network("fe80::/10"),  # IPv6 Link-Local
]


def is_ip_blocked(ip_str: str) -> bool:
    """Check if the given IP address falls into a forbidden network range."""
    try:
        ip = ipaddress.ip_address(ip_str)
        # Handle IPv4-mapped IPv6 (e.g., ::ffff:127.0.0.1 or ::ffff:169.254.169.254)
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in net for net in BLOCKED_IP_NETWORKS)
    except ValueError:
        return True


def _parse_alternate_ip(hostname: str) -> ipaddress.IPv4Address | None:
    """Parse decimal, hex, or octal IP representations."""
    # Decimal integer (e.g., 2130706433)
    if hostname.isdigit():
        try:
            val = int(hostname)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass
    # Hex integer (e.g., 0x7f000001)
    if hostname.lower().startswith("0x"):
        try:
            val = int(hostname, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass
    # Dotted octal/hex/decimal parts (e.g. 0177.0.0.1 or 0x7f.0.0.1)
    parts = hostname.split(".")
    if len(parts) == 4:
        try:
            parsed_bytes = []
            for p in parts:
                if p.lower().startswith("0x"):
                    b = int(p, 16)
                elif p.startswith("0") and len(p) > 1 and p.isdigit():
                    b = int(p, 8)
                elif p.isdigit():
                    b = int(p, 10)
                else:
                    return None
                if 0 <= b <= 255:
                    parsed_bytes.append(b)
                else:
                    return None
            return ipaddress.IPv4Address(bytes(parsed_bytes))
        except ValueError:
            pass
    return None


def validate_url_for_ssrf(url: str) -> bool:
    """Validate a target URL to protect against SSRF and host header forgery.

    Returns True if the URL is public and safe to fetch; raises SSRFSecurityViolation if unsafe.
    """
    if not url:
        raise SSRFSecurityViolation(url or "")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFSecurityViolation(url)

    # Reject embedded user credentials (e.g. http://user:pass@example.com)
    if parsed.username is not None or parsed.password is not None:
        raise SSRFSecurityViolation(url)

    hostname = parsed.hostname
    if not hostname:
        raise SSRFSecurityViolation(url)

    # Disallow localhost aliases
    if hostname.lower() in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"):
        raise SSRFSecurityViolation(url)

    # Check alternative IP encodings (decimal, hex, octal)
    alt_ip = _parse_alternate_ip(hostname)
    if alt_ip is not None and is_ip_blocked(str(alt_ip)):
        raise SSRFSecurityViolation(url)

    # Check direct IP addresses (including IPv6 and IPv4-mapped IPv6)
    if is_ip_blocked(hostname):
        raise SSRFSecurityViolation(url)

    # Resolve hostname to actual IP addresses and verify none are private/internal
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = str(sockaddr[0])
            if is_ip_blocked(ip_str):
                raise SSRFSecurityViolation(url)
    except (socket.gaierror, socket.herror):
        # Unresolvable domains cannot be reached safely
        raise SSRFSecurityViolation(url)

    return True


def verify_api_key(provided_key: str | None, configured_key: str | None) -> bool:
    """Verify API key using constant-time comparison to prevent timing attacks."""
    if not configured_key:
        return True
    if not provided_key:
        return False
    return secrets.compare_digest(provided_key.strip(), configured_key.strip())


async def _validate_response_and_redirects(response: httpx.Response) -> None:
    """Response hook to re-validate redirect target URLs and enforce size limits."""
    # Check Content-Length header against max allowed response size
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            settings = get_settings()
            if int(content_length) > settings.MAX_RESPONSE_BODY_BYTES:
                raise SSRFSecurityViolation(f"Response size exceeds limit: {response.url}")
        except ValueError:
            pass

    # Re-validate redirect target URL
    if response.is_redirect:
        location = response.headers.get("location")
        if location:
            target_url = str(response.url.join(location))
            validate_url_for_ssrf(target_url)


def create_safe_async_client() -> httpx.AsyncClient:
    """Create an AsyncClient configured with strict timeouts, redirect limits, and redirect validation."""
    settings = get_settings()
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
    timeout = httpx.Timeout(settings.HTTP_TIMEOUT_SECONDS, connect=4.0)

    return httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        follow_redirects=True,
        max_redirects=settings.MAX_REDIRECTS,
        event_hooks={"response": [_validate_response_and_redirects]},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36 ProspectBot/1.0"
            )
        },
    )
