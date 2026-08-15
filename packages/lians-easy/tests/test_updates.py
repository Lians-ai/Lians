from __future__ import annotations

import json
from io import BytesIO

import pytest
from lians_easy.updates import (
    MAX_RELEASE_BYTES,
    RELEASE_API,
    check_for_update,
    evaluate_release,
)


def _release(version: str, *, assets: list[str], prerelease: bool = False):
    return {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/Lians-ai/Lians/releases/tag/v{version}",
        "draft": False,
        "prerelease": prerelease,
        "assets": [
            {
                "name": name,
                "browser_download_url": (
                    f"https://github.com/Lians-ai/Lians/releases/download/v{version}/{name}"
                ),
            }
            for name in assets
        ],
    }


def test_release_selects_only_the_exact_windows_package_with_checksum():
    package = "Lians-Setup-0.6.0.exe"
    result = evaluate_release(
        _release("0.6.0", assets=[package, f"{package}.sha256", "source.zip"]),
        current_version="0.5.0",
        system="Windows",
        machine="AMD64",
    )

    assert result == {
        "status": "available",
        "current_version": "0.5.0",
        "available_version": "0.6.0",
        "release_url": "https://github.com/Lians-ai/Lians/releases/tag/v0.6.0",
        "package_name": package,
        "download_url": (
            "https://github.com/Lians-ai/Lians/releases/download/v0.6.0/"
            "Lians-Setup-0.6.0.exe"
        ),
        "checksum_published": True,
        "message": "A signed Lians desktop update is ready for this device.",
    }


@pytest.mark.parametrize(
    ("machine", "package"),
    [
        ("arm64", "Lians-0.6.0-macos-arm64.dmg"),
        ("x86_64", "Lians-0.6.0-macos-x86_64.dmg"),
    ],
)
def test_release_selects_the_native_macos_package(machine, package):
    result = evaluate_release(
        _release("0.6.0", assets=[package, f"{package}.sha256"]),
        current_version="0.5.0",
        system="Darwin",
        machine=machine,
    )
    assert result["status"] == "available"
    assert result["package_name"] == package


def test_release_never_offers_missing_checksum_prerelease_or_unofficial_urls():
    package = "Lians-Setup-0.6.0.exe"
    missing_checksum = evaluate_release(
        _release("0.6.0", assets=[package]),
        current_version="0.5.0",
        system="Windows",
        machine="AMD64",
    )
    assert missing_checksum["status"] == "not_published"

    with pytest.raises(ValueError, match="not a stable release"):
        evaluate_release(
            _release("0.6.0", assets=[package, f"{package}.sha256"], prerelease=True),
            current_version="0.5.0",
            system="Windows",
            machine="AMD64",
        )

    malicious = _release("0.6.0", assets=[package, f"{package}.sha256"])
    malicious["assets"][0]["browser_download_url"] = "https://attacker.example/Lians.exe"
    with pytest.raises(ValueError, match="not an official GitHub URL"):
        evaluate_release(
            malicious,
            current_version="0.5.0",
            system="Windows",
            machine="AMD64",
        )


def test_current_or_unsupported_install_never_gets_a_download():
    current = evaluate_release(
        _release("0.5.0", assets=[]),
        current_version="0.5.0",
        system="Windows",
        machine="AMD64",
    )
    assert current["status"] == "up_to_date"
    assert "download_url" not in current

    unsupported = evaluate_release(
        _release("0.6.0", assets=[]),
        current_version="0.5.0",
        system="Linux",
        machine="x86_64",
    )
    assert unsupported["status"] == "unsupported"
    assert "download_url" not in unsupported


class _Response:
    def __init__(self, payload: bytes, *, declared: int | None = None) -> None:
        self._payload = BytesIO(payload)
        self.headers = {"Content-Length": str(declared if declared is not None else len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount: int) -> bytes:
        return self._payload.read(amount)


def test_network_check_is_fixed_to_official_api_and_size_bounded():
    package = "Lians-Setup-0.6.0.exe"
    payload = json.dumps(_release("0.6.0", assets=[package, f"{package}.sha256"])).encode()
    calls = []

    def opener(request, *, timeout):
        calls.append((request.full_url, timeout, request.get_header("User-agent")))
        return _Response(payload)

    result = check_for_update(
        current_version="0.5.0",
        system="Windows",
        machine="AMD64",
        opener=opener,
    )
    assert result["status"] == "available"
    assert calls == [(RELEASE_API, 5, "Lians-Bridge/0.5.0")]

    with pytest.raises(ValueError, match="too large"):
        check_for_update(
            current_version="0.5.0",
            system="Windows",
            machine="AMD64",
            opener=lambda *args, **kwargs: _Response(b"{}", declared=MAX_RELEASE_BYTES + 1),
        )
