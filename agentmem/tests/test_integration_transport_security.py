"""Integration outbox SSRF and DNS-rebinding transport invariants."""

import ipaddress
import socket

import pytest
from lians.integration_service import (
    IntegrationConfigurationError,
    _pinned_destination_url,
    _resolve_destination_addresses,
)


def test_pinned_https_url_preserves_original_host_authority_and_sni_target():
    pinned, host = _pinned_destination_url(
        "https://events.example.test/hooks/lians",
        "events.example.test",
        443,
        ipaddress.ip_address("93.184.216.34"),
    )

    assert pinned == "https://93.184.216.34/hooks/lians"
    assert host == "events.example.test"


def test_pinned_ipv6_url_and_nondefault_port_are_canonical():
    pinned, host = _pinned_destination_url(
        "https://events.example.test:8443/hooks/lians",
        "events.example.test",
        8443,
        ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946"),
    )

    assert pinned.startswith("https://[2606:2800:220:1:248:1893:25c8:1946]:8443/")
    assert host == "events.example.test:8443"


@pytest.mark.asyncio
async def test_mixed_public_and_loopback_dns_answers_fail_closed(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(IntegrationConfigurationError, match="blocked network"):
        await _resolve_destination_addresses("https://events.example.test/hooks")


@pytest.mark.asyncio
async def test_resolution_returns_only_validated_connect_time_addresses(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    host, port, addresses = await _resolve_destination_addresses(
        "https://events.example.test/hooks"
    )

    assert host == "events.example.test"
    assert port == 443
    assert [str(address) for address in addresses] == [
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    ]
