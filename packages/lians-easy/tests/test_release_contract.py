from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPOSITORY_ROOT / "packages" / "lians-easy"


def _package_version() -> str:
    pyproject = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_public_python_package_has_product_aligned_commands() -> None:
    pyproject = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires = ["hatchling==1.32.0"]' in pyproject
    assert 'name = "lians-bridge"' in pyproject
    assert 'description = "Less repeated context for the AI tools you already use"' in pyproject
    assert 'lians = "lians_easy.cli:main"' in pyproject
    assert 'lians-bridge = "lians_easy.cli:main"' in pyproject
    assert 'lians-easy = "lians_easy.cli:main"' in pyproject
    assert 'Repository = "https://github.com/Lians-ai/Lians"' in pyproject


def test_public_python_publish_is_gated_and_exercises_the_exact_wheel() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "publish-lians-bridge.yml"
    ).read_text(encoding="utf-8")

    assert 'release_tag must be an existing stable semver tag (vX.Y.Z)' in workflow
    assert '"lians-bridge"' in workflow
    assert 'Runtime version $runtime_version does not match package version' in workflow
    assert 'git rev-list -n 1 "$RELEASE_TAG"' in workflow
    assert 'git rev-parse HEAD' in workflow
    assert 'python -m twine check "$wheel" "$source_archive"' in workflow
    assert 'lians_bridge-$PACKAGE_VERSION-py3-none-any.whl' in workflow
    assert 'Expected exactly one wheel and one source archive' in workflow
    assert 'bin/lians" doctor --json' in workflow
    assert 'bin/lians-bridge" doctor --json' in workflow
    assert 'bin/lians-easy" doctor --json' in workflow
    assert 'joinpath("app", "index.html")' in workflow
    assert "vars.PUBLISH_LIANS_BRIDGE_PYPI == 'true'" in workflow
    assert "environment: pypi-lians-bridge" in workflow
    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish@" in workflow
    assert "skip-existing" not in workflow
    assert 'bin/lians" --version' in workflow
    assert 'bin/lians-bridge" --version' in workflow
    assert 'bin/lians-easy" --version' in workflow


def test_windows_identity_resources_match_package_version() -> None:
    version = _package_version()
    numeric = tuple(int(part) for part in version.split(".")) + (0,)
    resource = (PACKAGE_ROOT / "windows-version-info.txt").read_text(encoding="utf-8")

    assert f"filevers={numeric}" in resource
    assert f"prodvers={numeric}" in resource
    assert f"StringStruct('FileVersion', '{version}')" in resource
    assert f"StringStruct('ProductVersion', '{version}')" in resource
    assert "StringStruct('ProductName', 'Lians Bridge')" in resource
    assert "StringStruct('OriginalFilename', 'LiansMemory.exe')" in resource


def test_windows_packaging_uses_verified_official_nsis_archive() -> None:
    installer = (PACKAGE_ROOT / "scripts" / "install_nsis.ps1").read_text(
        encoding="utf-8"
    )
    build_workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "build-lians-easy.yml"
    ).read_text(encoding="utf-8")
    release_workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert "https://downloads.sourceforge.net/project/nsis/" in installer
    assert "NSIS%203/3.12/nsis-3.12.zip" in installer
    assert "56581f90db321581c5381193d796fffcf2d24b2f8fed2160a6c6a3baa67f2c4f" in installer
    assert "--retry 5" in installer
    assert "Get-FileHash -Algorithm SHA256" in installer
    assert 'if ($actualVersion -ne "v$nsisVersion")' in installer
    assert "choco install nsis" not in build_workflow
    assert "choco install nsis" not in release_workflow
    assert "packages/lians-easy/scripts/install_nsis.ps1" in build_workflow
    assert "packages/lians-easy/scripts/install_nsis.ps1" in release_workflow


def test_windows_icon_contains_standard_shell_sizes() -> None:
    icon = (PACKAGE_ROOT / "windows-lians.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", icon[:6])
    assert (reserved, kind) == (0, 1)

    sizes: list[int] = []
    for index in range(count):
        offset = 6 + index * 16
        width, height, _, _, _, bits, length, image_offset = struct.unpack(
            "<BBBBHHII", icon[offset : offset + 16]
        )
        width = width or 256
        height = height or 256
        assert width == height
        assert bits == 32
        assert length > 0
        assert image_offset + length <= len(icon)
        sizes.append(width)

    assert sizes == [16, 24, 32, 48, 64, 128, 256]


def test_stable_release_signs_and_verifies_windows_installer_before_upload() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    desktop_job = workflow.split("  lians-easy:\n", 1)[1].split(
        "\n  lians-easy-macos:", 1
    )[0]

    assert "vars.PUBLISH_SIGNED_LIANS_DESKTOP == 'true'" in desktop_job
    assert "AZURE_ARTIFACT_SIGNING_CLIENT_ID" in desktop_job
    assert "AZURE_ARTIFACT_SIGNING_TENANT_ID" in desktop_job
    assert "AZURE_ARTIFACT_SIGNING_SUBSCRIPTION_ID" in desktop_job
    assert "AZURE_ARTIFACT_SIGNING_ENDPOINT" in desktop_job
    assert "AZURE_ARTIFACT_SIGNING_ACCOUNT" in desktop_job
    assert "AZURE_ARTIFACT_SIGNING_PROFILE" in desktop_job
    assert "WINDOWS_SIGNING_SUBJECT" in desktop_job
    assert "azure/login@a457da9ea143d694b1b9c7c869ebb04ebe844ef5" in desktop_job
    assert (
        "Azure/artifact-signing-action@c7ab2a863ab5f9a846ddb8265964877ef296ee82"
        in desktop_job
    )
    assert "timestamp-rfc3161:" in desktop_job
    assert "dist/windows-companion/Lians.exe" in desktop_job
    assert "dist/windows-companion/LiansApp/Lians.exe" in desktop_job
    assert "dist/windows-companion/LiansApp/LiansMemory.exe" in desktop_job
    assert "build_windows_companion.ps1" in desktop_job
    assert "uv==0.11.26" in desktop_job
    assert "uv sync --project packages/lians-easy --frozen --group build --python $buildPython" in desktop_job
    assert "build_windows_installer.ps1" in desktop_job
    assert "artifact_windows_installer_smoke.py" in desktop_job
    assert "--rollback-fixture" in desktop_job
    assert "--expected-signer-subject" in desktop_job
    assert "Lians-Setup-*.exe" in desktop_job
    assert "Get-AuthenticodeSignature" in desktop_job
    assert "signature.Status -ne 'Valid'" in desktop_job
    assert "SignerCertificate.Subject" in desktop_job
    assert "Attest signed Windows installer build provenance" in desktop_job
    assert "gh attestation verify" in desktop_job
    assert "Refusing to overwrite existing release asset" in desktop_job
    assert "--clobber" not in desktop_job
    assert desktop_job.index(
        "Sign the Windows installer with Artifact Signing"
    ) < desktop_job.index("gh release upload")

    pull_request_workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "build-lians-easy.yml"
    ).read_text(encoding="utf-8")
    assert 'artifact: LiansMemory-windows\n            python_version: "3.11"' in (
        pull_request_workflow
    )
    assert "python-version: ${{ matrix.python_version }}" in pull_request_workflow
    companion_builder = (
        PACKAGE_ROOT / "scripts" / "build_windows_companion.ps1"
    ).read_text(encoding="utf-8")
    for build_contract in (workflow, pull_request_workflow):
        assert "build_windows_companion.ps1" in build_contract
        assert "artifact_portability_smoke.py" in build_contract
        assert "Verify Windows package identity" in build_contract
        assert "artifact_app_smoke.py" in build_contract
        assert "uv sync --project packages/lians-easy --frozen --group build --python $buildPython" in build_contract
    assert "--add-data" in companion_builder
    assert "lians_easy/app" in companion_builder
    assert "windows-lians.ico" in companion_builder
    assert "windows-version-info.txt" in companion_builder


def test_native_macos_packages_are_exercised_on_both_architectures() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "build-lians-easy.yml"
    ).read_text(encoding="utf-8")

    assert "os: macos-15\n" in workflow
    assert "os: macos-15-intel\n" in workflow
    assert "artifact: LiansMemory-macos-arm64" in workflow
    assert "artifact: LiansMemory-macos-x86_64" in workflow
    assert "build_macos_dmg.sh" in workflow
    assert "artifact_macos_dmg_smoke.py" in workflow
    assert "dist/installer/Lians-*.dmg" in workflow
    assert "OPENSSL_STATIC=1" in workflow
    assert "--no-binary cryptography" in workflow
    assert "cryptography==50.0.0" in workflow


def test_install_free_linux_package_is_built_exercised_and_attested() -> None:
    pull_request_workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "build-lians-easy.yml"
    ).read_text(encoding="utf-8")
    release_workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    release_job = release_workflow.split("  lians-easy-linux:\n", 1)[1].split(
        "\n  go-tag:", 1
    )[0]

    for contract in (pull_request_workflow, release_job):
        assert "appimagetool/releases/download/1.9.1" in contract
        assert (
            "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
            in contract
        )
        assert "type2-runtime/releases/download/20251108/runtime-x86_64" in contract
        assert (
            "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
            in contract
        )
        assert "build_linux_appimage.sh" in contract
        assert "artifact_linux_appimage_smoke.py" in contract
        assert "Lians-$version-linux-x86_64.AppImage" in contract

    script = (PACKAGE_ROOT / "scripts" / "build_linux_appimage.sh").read_text(
        encoding="utf-8"
    )
    smoke = (PACKAGE_ROOT / "tests" / "artifact_linux_appimage_smoke.py").read_text(
        encoding="utf-8"
    )
    assert "Lians-$version-linux-$architecture.AppImage" in script
    assert "Terminal=false" in script
    assert "AppRun" in script
    assert '--runtime-file "$runtime_file"' in script
    assert "--appimage-extract" in smoke
    assert "artifact_app_smoke.py" in smoke

    assert "vars.PUBLISH_ATTESTED_LIANS_LINUX == 'true'" in release_job
    assert "environment: linux-lians-desktop" in release_job
    assert "id-token: write" in release_job
    assert "attestations: write" in release_job
    assert (
        "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
        in release_job
    )
    assert "gh attestation verify" in release_job
    assert "sha256sum" in release_job
    assert "gh release upload" in release_job


def test_macos_drag_and_drop_package_has_stable_identity_and_architecture() -> None:
    script = (PACKAGE_ROOT / "scripts" / "build_macos_dmg.sh").read_text(
        encoding="utf-8"
    )
    smoke = (PACKAGE_ROOT / "tests" / "artifact_macos_dmg_smoke.py").read_text(
        encoding="utf-8"
    )

    assert "ai.lians.memory" in script
    assert "CFBundleDisplayName string Lians" in script
    assert "LSMinimumSystemVersion string 13.0" in script
    assert 'ln -s /Applications "$volume_root/Applications"' in script
    assert 'actual_architectures="$(lipo -archs "$binary")"' in script
    assert "codesign --verify --deep --strict" in script
    assert "hdiutil create" in script
    assert "Lians-$version-macos-$architecture.dmg" in script
    assert "artifact_app_smoke.py" in smoke
    assert 'os.readlink(applications) == "/Applications"' in smoke


def test_stable_macos_release_requires_developer_id_and_notarization() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    macos_job = workflow.split("  lians-easy-macos:\n", 1)[1].split(
        "\n  go-tag:", 1
    )[0]

    assert "vars.PUBLISH_SIGNED_LIANS_MACOS == 'true'" in macos_job
    assert "runner: macos-15\n" in macos_job
    assert "runner: macos-15-intel\n" in macos_job
    assert "MACOS_SIGNING_CERT_P12_BASE64" in macos_job
    assert "MACOS_SIGNING_CERT_PASSWORD" in macos_job
    assert "MACOS_SIGNING_IDENTITY" in macos_job
    assert "MACOS_SIGNING_TEAM_ID" in macos_job
    assert "APPLE_NOTARY_KEY_P8_BASE64" in macos_job
    assert "APPLE_NOTARY_KEY_ID" in macos_job
    assert "APPLE_NOTARY_ISSUER_ID" in macos_job
    assert '--codesign-identity "$MACOS_SIGNING_IDENTITY"' in macos_job
    assert "OPENSSL_STATIC=1" in macos_job
    assert "--no-binary cryptography" in macos_job
    assert "cryptography==50.0.0" in macos_job
    assert "artifact_macos_dmg_smoke.py" in macos_job
    assert "--expected-signing-identity" in macos_job
    assert "--expected-team-id" in macos_job
    assert "xcrun notarytool submit" in macos_job
    assert 'result.get("status") != "Accepted"' in macos_job
    assert "xcrun stapler staple" in macos_job
    assert "xcrun stapler validate" in macos_job
    assert "--require-notarized" in macos_job
    assert "shasum -a 256" in macos_job
    assert macos_job.index('--codesign-identity "$MACOS_SIGNING_IDENTITY"') < (
        macos_job.index("xcrun notarytool submit")
    )
    assert macos_job.index("xcrun stapler validate") < macos_job.index(
        "gh release upload"
    )


def test_windows_installer_is_per_user_and_separates_app_removal_from_erasure() -> None:
    script = (PACKAGE_ROOT / "windows-installer.nsi").read_text(encoding="utf-8")

    assert 'RequestExecutionLevel user' in script
    assert 'InstallDir "$LOCALAPPDATA\\Programs\\Lians"' in script
    assert "MUI_PAGE_DIRECTORY" not in script
    assert 'MUI_FINISHPAGE_RUN_TEXT "Open Lians"' in script
    assert 'CreateShortcut "$SMPROGRAMS\\Lians\\Lians.lnk"' in script
    assert "uninstall --clients all --yes" in script
    assert 'ReadEnvStr $8 "LIANS_EASY_HOME"' in script
    assert 'Delete "$8\\${PRODUCT_RUNTIME}"' in script
    assert 'DeleteRegValue HKCU "${PRODUCT_STARTUP_KEY}" "Lians"' in script
    assert "IfSilent UninstallFinished" in script
    assert "Permanently erase all encrypted Lians memories" in script
    assert 'RMDir /r "$LOCALAPPDATA\\Lians"' in script
    assert script.index("IfSilent UninstallFinished") < script.index(
        'RMDir /r "$LOCALAPPDATA\\Lians"'
    )


def test_windows_installer_health_checks_upgrades_and_restores_failed_candidates() -> None:
    script = (PACKAGE_ROOT / "windows-installer.nsi").read_text(encoding="utf-8")
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "build-lians-easy.yml"
    ).read_text(encoding="utf-8")

    assert "PRODUCT_SHUTDOWN_EVENT" in script
    assert "PRODUCT_CANDIDATE_APP" in script
    assert "PRODUCT_PREVIOUS_APP" in script
    assert "PRODUCT_PREVIOUS_LAUNCHER" in script
    assert 'File /r "${LIANS_APP_BUNDLE}\\*"' in script
    assert "doctor --json" in script
    assert 'Rename "$INSTDIR\\${PRODUCT_PREVIOUS_APP}" "$INSTDIR\\${PRODUCT_APP_DIR}"' in script
    assert script.index("doctor --json") < script.index("WriteUninstaller")
    assert "SetErrorLevel 1603" in script
    assert "--rollback-fixture" in workflow
    assert "-RuntimeOverride packages/lians-easy/README.md" in workflow


def test_packaged_control_center_is_source_pinned_and_bounded() -> None:
    app_root = PACKAGE_ROOT / "lians_easy" / "app"
    manifest = json.loads((app_root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["source"]["commit"] == "8dd0a27e167ed19b94877224d5f3d1cbda2c6ce5"
    assert manifest["source"]["sites_version"] == 31
    assert manifest["source"]["build"] == "npm run build:local"

    expected = set(manifest["files"])
    actual = {
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    assert actual == expected

    total_bytes = 0
    for relative, record in manifest["files"].items():
        payload = (app_root / relative).read_bytes()
        assert len(payload) == record["bytes"]
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
        total_bytes += len(payload)
    assert total_bytes < 420_000

    scripts = [relative for relative in expected if relative.endswith(".js")]
    assert len(scripts) == 2
    script_documents = {
        relative: (app_root / relative).read_text(encoding="utf-8")
        for relative in scripts
    }
    script = next(value for value in script_documents.values() if "MEMORY CONTROL CENTER" in value)
    assert "\u00b7" in script
    assert "\u00c2\u00b7" not in script
    assert "/v1/integrations/disconnect" in script
    assert "/v1/privacy/erase" in script
    assert "/v1/update" in script
    assert "/v1/update/download" in script
    assert "/v1/update/open" in script
    assert "/v1/backups/export" in script
    assert "/v1/backups/verify" in script
    assert "/v1/backups/import" in script

    cloud_script = script_documents["assets/cloud-controls.js"]
    assert "/v1/cloud/status" in cloud_script
    assert "/v1/cloud/sign-in" in cloud_script
    assert "/v1/cloud/sync" in cloud_script
    assert "/v1/cloud/sign-out" in cloud_script
    assert "/v1/cloud/delete" in cloud_script
    assert "/v1/cloud/device-enrollment/start" in cloud_script
    assert "/v1/cloud/device-enrollment/check" in cloud_script
    assert "/v1/cloud/device-enrollment/cancel" in cloud_script
    assert "/v1/cloud/device-requests" in cloud_script
    assert "/v1/cloud/device-requests/approve" in cloud_script
    assert "/v1/cloud/devices" in cloud_script
    assert "/v1/cloud/devices/remove" in cloud_script
    assert "/v1/backups/verify" in cloud_script
    assert "/v1/backups/import" in cloud_script
    assert "const MAX_RECOVERY_BACKUP_BYTES = 32 * 1024 * 1024" in cloud_script
    assert "confirmed: true" in cloud_script
    assert "recover_cloud: true" in cloud_script
    assert "Memory already saved on this device may remain there" in cloud_script
    assert "Removing a device gives every remaining device a new key" in cloud_script
    assert "Recover from encrypted backup" in cloud_script
    assert "Lians cannot reset that encryption" in cloud_script
    assert "old encrypted cloud copy may remain" in cloud_script
    assert "Add this device" in cloud_script
    assert "Code matches · approve" in cloud_script
    assert "Lians cannot read it" in cloud_script
    assert "local memory was not changed" in cloud_script
    assert "innerHTML" not in cloud_script
    assert "Authorization" not in cloud_script
    assert "client_secret" not in cloud_script
    assert "Nothing existing will be overwritten" in script
    assert "ERASE ALL LIANS MEMORY" in script
    assert "checks only when you ask" in script
    assert "Download verified update" in script
    assert "Downloading never opens an installer" in script
