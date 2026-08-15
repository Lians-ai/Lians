from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from lians_easy.updates import (
    MAX_CHECKSUM_BYTES,
    MAX_PACKAGE_BYTES,
    MAX_RELEASE_BYTES,
    RELEASE_API,
    check_for_update,
    download_verified_update,
    evaluate_release,
    inspect_platform_trust,
    open_prepared_update,
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
        "checksum_url": (
            "https://github.com/Lians-ai/Lians/releases/download/v0.6.0/"
            "Lians-Setup-0.6.0.exe.sha256"
        ),
        "checksum_published": True,
        "message": "A verified Lians desktop update is ready for this device.",
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


def test_release_selects_the_install_free_linux_package():
    package = "Lians-0.6.0-linux-x86_64.AppImage"
    result = evaluate_release(
        _release("0.6.0", assets=[package, f"{package}.sha256"]),
        current_version="0.5.0",
        system="Linux",
        machine="x86_64",
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

    duplicate = _release("0.6.0", assets=[package, package, f"{package}.sha256"])
    with pytest.raises(ValueError, match="duplicate desktop assets"):
        evaluate_release(
            duplicate,
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
        machine="aarch64",
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


def _available_release(*, system="Windows", machine="AMD64"):
    package = (
        "Lians-Setup-0.6.0.exe"
        if system == "Windows"
        else "Lians-0.6.0-linux-x86_64.AppImage"
    )
    return evaluate_release(
        _release("0.6.0", assets=[package, f"{package}.sha256"]),
        current_version="0.5.0",
        system=system,
        machine=machine,
    )


def test_confirmed_download_verifies_checksum_and_never_overwrites(tmp_path):
    release = _available_release()
    package = b"signed installer bytes"
    digest = hashlib.sha256(package).hexdigest()
    existing = tmp_path / release["package_name"]
    existing.write_bytes(b"keep the original")
    calls = []

    def opener(request, *, timeout):
        calls.append((request.full_url, timeout))
        if request.full_url.endswith(".sha256"):
            return _Response(f"{digest}  {release['package_name']}\n".encode())
        return _Response(package)

    result = download_verified_update(
        release,
        destination_dir=tmp_path,
        system="Windows",
        machine="AMD64",
        opener=opener,
        trust_checker=lambda path: {
            "trust": "publisher_verified",
            "can_open": True,
            "trust_message": "Publisher verified.",
        },
    )

    assert existing.read_bytes() == b"keep the original"
    assert result["package_name"] == "Lians-Setup-0.6.0 (1).exe"
    assert Path(result["path"]).read_bytes() == package
    assert result["sha256"] == digest
    assert result["saved_location"] == "Downloads"
    assert result["can_open"] is True
    assert [url for url, _ in calls] == [release["checksum_url"], release["download_url"]]


@pytest.mark.parametrize(
    ("checksum", "message"),
    [
        (b"0" * 64 + b"  wrong.exe\n", "does not match this package"),
        (b"0" * 64 + b"  Lians-Setup-0.6.0.exe\nextra\n", "ambiguous"),
    ],
)
def test_download_rejects_wrong_or_ambiguous_checksum(tmp_path, checksum, message):
    release = _available_release()

    def opener(request, *, timeout):
        if request.full_url.endswith(".sha256"):
            return _Response(checksum)
        raise AssertionError("the package must not download before its checksum is accepted")

    with pytest.raises(ValueError, match=message):
        download_verified_update(
            release,
            destination_dir=tmp_path,
            system="Windows",
            machine="AMD64",
            opener=opener,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("suffix", "declared"),
    [(".sha256", MAX_CHECKSUM_BYTES + 1), (".exe", MAX_PACKAGE_BYTES + 1)],
)
def test_download_enforces_checksum_and_package_size_bounds(tmp_path, suffix, declared):
    release = _available_release()
    package = b"installer"
    digest = hashlib.sha256(package).hexdigest()

    def opener(request, *, timeout):
        if request.full_url.endswith(".sha256"):
            payload = f"{digest}  {release['package_name']}\n".encode()
            return _Response(payload, declared=declared if suffix == ".sha256" else None)
        return _Response(package, declared=declared if suffix == ".exe" else None)

    with pytest.raises(ValueError, match="too large"):
        download_verified_update(
            release,
            destination_dir=tmp_path,
            system="Windows",
            machine="AMD64",
            opener=opener,
        )
    assert not list(tmp_path.glob(".lians-update-*.part"))


def test_download_revalidates_urls_and_device_before_network_or_disk(tmp_path):
    release = _available_release()
    release["checksum_url"] = "https://attacker.example/checksum"

    with pytest.raises(ValueError, match="official GitHub URL"):
        download_verified_update(
            release,
            destination_dir=tmp_path,
            system="Windows",
            machine="AMD64",
            opener=lambda *args, **kwargs: pytest.fail("network must not be used"),
        )
    assert list(tmp_path.iterdir()) == []


def test_open_rehashes_prepared_update_and_uses_separate_trust_gate(tmp_path):
    package = tmp_path / "Lians-Setup-0.6.0.exe"
    package.write_bytes(b"verified")
    prepared = {"path": str(package), "sha256": hashlib.sha256(b"verified").hexdigest()}
    launches = []

    result = open_prepared_update(
        prepared,
        system="Windows",
        trust_checker=lambda path: {
            "trust": "publisher_verified",
            "can_open": True,
            "trust_message": "Publisher verified.",
        },
        launcher=lambda path, can_open, system: launches.append((path, can_open, system)),
    )
    assert result["status"] == "opened"
    assert launches == [(package, True, "Windows")]

    result = open_prepared_update(
        prepared,
        system="Linux",
        trust_checker=lambda path: {
            "trust": "checksum_verified",
            "can_open": False,
            "trust_message": "Checksum verified.",
        },
        launcher=lambda path, can_open, system: launches.append((path, can_open, system)),
    )
    assert result["status"] == "revealed"
    assert launches[-1] == (package, False, "Linux")

    package.write_bytes(b"changed after download")
    with pytest.raises(ValueError, match="changed"):
        open_prepared_update(
            prepared,
            system="Windows",
            launcher=lambda *args: pytest.fail("changed package must never open"),
        )


def test_windows_publisher_must_match_the_signed_installed_app(tmp_path, monkeypatch):
    candidate = tmp_path / "Lians-Setup-0.6.0.exe"
    installed = tmp_path / "LiansMemory.exe"
    candidate.write_bytes(b"candidate")
    installed.write_bytes(b"installed")
    monkeypatch.setattr("lians_easy.updates.shutil.which", lambda name: "powershell.exe")
    outputs = iter(
        [
            '{"Status":"Valid","Subject":"CN=Lians"}',
            '{"Status":"Valid","Subject":"CN=Lians"}',
        ]
    )

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")

    trusted = inspect_platform_trust(
        candidate,
        system="Windows",
        current_executable=installed,
        runner=runner,
    )
    assert trusted["trust"] == "publisher_verified"
    assert trusted["can_open"] is True

    outputs = iter(
        [
            '{"Status":"Valid","Subject":"CN=Lians"}',
            '{"Status":"Valid","Subject":"CN=Someone Else"}',
        ]
    )
    untrusted = inspect_platform_trust(
        candidate,
        system="Windows",
        current_executable=installed,
        runner=runner,
    )
    assert untrusted["trust"] == "checksum_verified"
    assert untrusted["can_open"] is False


def test_macos_update_requires_same_team_gatekeeper_and_stapled_ticket(tmp_path):
    candidate = tmp_path / "Lians-0.6.0-macos-arm64.dmg"
    installed = tmp_path / "LiansMemory"
    candidate.write_bytes(b"candidate")
    installed.write_bytes(b"installed")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "/usr/bin/codesign":
            return SimpleNamespace(returncode=0, stdout="", stderr="TeamIdentifier=LIANSTEAM\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    trusted = inspect_platform_trust(
        candidate,
        system="Darwin",
        current_executable=installed,
        runner=runner,
    )
    assert trusted["trust"] == "publisher_verified"
    assert trusted["can_open"] is True
    assert [call[0] for call in calls] == [
        "/usr/bin/codesign",
        "/usr/bin/codesign",
        "/usr/sbin/spctl",
        "/usr/bin/xcrun",
    ]
