#!/usr/bin/env python3
"""Fail unless every lock-step Lians release surface declares one version."""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
VERIFIED_MCPB_PUBLICATION = ("0.4.1", "0.4.1")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def _match(path: pathlib.Path, pattern: str, label: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"could not find {label} version in {path.relative_to(ROOT)}")
    return match.group(1)


def _literal_assignment(path: pathlib.Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            try:
                return ast.literal_eval(node.value)
            except (TypeError, ValueError) as exc:
                raise SystemExit(
                    f"{path.relative_to(ROOT)} must assign a literal {name}"
                ) from exc
    raise SystemExit(f"could not find {name} in {path.relative_to(ROOT)}")


def _uv_package_version(path: pathlib.Path, package_name: str) -> str:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    matches = [
        package.get("version")
        for package in document.get("package", [])
        if package.get("name") == package_name
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise SystemExit(
            f"{path.relative_to(ROOT)} must contain exactly one versioned "
            f"{package_name!r} package"
        )
    return matches[0]


def verify_migration_contract() -> str:
    expected = _literal_assignment(
        ROOT / "agentmem/src/lians/version.py", "EXPECTED_ALEMBIC_HEAD"
    )
    if not isinstance(expected, str) or not expected:
        raise SystemExit("EXPECTED_ALEMBIC_HEAD must be a non-empty string")

    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted((ROOT / "agentmem/alembic/versions").glob("*.py")):
        revision = _literal_assignment(path, "revision")
        down_revision = _literal_assignment(path, "down_revision")
        if not isinstance(revision, str) or not revision:
            raise SystemExit(f"invalid revision in {path.relative_to(ROOT)}")
        if len(revision) > 32:
            raise SystemExit(
                f"Alembic revision {revision!r} exceeds the default "
                "alembic_version.version_num VARCHAR(32) capacity"
            )
        if re.fullmatch(r"[A-Za-z0-9_]+", revision) is None:
            raise SystemExit(
                f"Alembic revision {revision!r} must use portable identifier characters"
            )
        if revision in revisions:
            raise SystemExit(f"duplicate Alembic revision: {revision}")
        revisions.add(revision)
        if down_revision is None:
            continue
        parent_values = (
            down_revision if isinstance(down_revision, (tuple, list)) else [down_revision]
        )
        if not parent_values or not all(
            isinstance(parent, str) and parent for parent in parent_values
        ):
            raise SystemExit(f"invalid down_revision in {path.relative_to(ROOT)}")
        parents.update(parent_values)

    missing_parents = parents - revisions
    if missing_parents:
        raise SystemExit(f"Alembic graph references missing revisions: {missing_parents}")
    heads = revisions - parents
    if heads != {expected}:
        raise SystemExit(
            f"EXPECTED_ALEMBIC_HEAD={expected!r} disagrees with graph heads {sorted(heads)}"
        )
    return expected


def verify_python_distribution_contract() -> None:
    """Keep the deployable platform distinct from the published client SDK."""
    platform = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdk = tomllib.loads(
        (ROOT / "agentmem/sdk/python/pyproject.toml").read_text(encoding="utf-8")
    )
    compatibility = tomllib.loads(
        (ROOT / "sdk/python/pyproject.toml").read_text(encoding="utf-8")
    )
    names = {
        "platform": platform["project"]["name"],
        "python_sdk": sdk["project"]["name"],
        "compatibility_private": compatibility["project"]["name"],
    }
    expected = {
        "platform": "lians-platform",
        "python_sdk": "lians-sdk",
        "compatibility_private": "lians",
    }
    if names != expected:
        raise SystemExit(
            "Python distribution identities drifted from the platform/SDK contract: "
            f"expected {expected}, found {names}"
        )
    classifiers = set(compatibility["project"].get("classifiers", []))
    if "Private :: Do Not Upload" not in classifiers:
        raise SystemExit("the source-only lians compatibility package must remain private")


def verify_mcpb_downstream_contract(release_version: str) -> tuple[str, str]:
    """Validate the independently released MCPB without pretending it is lock-step."""
    directory = ROOT / "integrations/mcpb"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))
    bundle_versions = {
        "manifest": manifest.get("version"),
        "project": project["project"].get("version"),
        "lock": _uv_package_version(
            directory / "uv.lock", "lians-agent-memory-mcpb"
        ),
    }
    if len(set(bundle_versions.values())) != 1:
        raise SystemExit(f"MCPB bundle metadata is internally inconsistent: {bundle_versions}")
    bundle_version = next(iter(bundle_versions.values()))
    if not isinstance(bundle_version, str) or SEMVER.fullmatch(bundle_version) is None:
        raise SystemExit(f"unsupported MCPB bundle version: {bundle_version!r}")

    sdk_dependencies = [
        dependency
        for dependency in project["project"].get("dependencies", [])
        if dependency.startswith("lians-sdk[")
    ]
    if len(sdk_dependencies) != 1:
        raise SystemExit("MCPB must declare exactly one extras-enabled lians-sdk pin")
    pin_match = re.search(r"==([^;\s]+)", sdk_dependencies[0])
    if pin_match is None:
        raise SystemExit("MCPB lians-sdk dependency must be an exact registry pin")
    sdk_pin = pin_match.group(1)
    locked_sdk = _uv_package_version(directory / "uv.lock", "lians-sdk")
    if sdk_pin != locked_sdk:
        raise SystemExit(
            f"MCPB pins lians-sdk {sdk_pin}, but its lock resolves {locked_sdk}"
        )
    if (bundle_version, sdk_pin) != VERIFIED_MCPB_PUBLICATION:
        verified_bundle, verified_sdk = VERIFIED_MCPB_PUBLICATION
        raise SystemExit(
            "MCPB metadata moved beyond the independently verified publication "
            f"pair bundle={verified_bundle}, sdk={verified_sdk}; update the guard "
            "only after the new SDK is resolvable from PyPI and the rebuilt MCPB "
            "has been verified in a clean host"
        )

    def release_core(value: str) -> tuple[int, int, int]:
        core = value.split("+", 1)[0].split("-", 1)[0]
        return tuple(int(part) for part in core.split("."))  # type: ignore[return-value]

    release_core_version = release_core(release_version)
    if release_core(bundle_version) > release_core_version:
        raise SystemExit("MCPB bundle version cannot be ahead of the platform release")
    if release_core(sdk_pin) > release_core_version:
        raise SystemExit("MCPB cannot pin an SDK newer than the platform release")
    return bundle_version, sdk_pin


def declared_versions() -> dict[str, str]:
    pom = ET.parse(ROOT / "agentmem/sdk/java/pom.xml").getroot()
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    java_version = pom.findtext("m:version", namespaces=namespace)
    if java_version is None:
        raise SystemExit("could not find Java project version")

    typescript_lock = json.loads(
        (ROOT / "agentmem/sdk/typescript/package-lock.json").read_text(
            encoding="utf-8"
        )
    )
    try:
        typescript_lock_root = typescript_lock["packages"][""]["version"]
    except (KeyError, TypeError) as exc:
        raise SystemExit("TypeScript lockfile is missing its root package version") from exc

    mcp_manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    mcp_packages = mcp_manifest.get("packages")
    if not isinstance(mcp_packages, list) or len(mcp_packages) != 1:
        raise SystemExit("server.json must declare exactly one release package")
    mcp_package_version = mcp_packages[0].get("version")
    if not isinstance(mcp_package_version, str):
        raise SystemExit("server.json package must declare a version")

    return {
        "platform": tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"],
        "platform_lock": _uv_package_version(
            ROOT / "uv.lock", "lians-platform"
        ),
        "platform_runtime": _match(
            ROOT / "agentmem/src/lians/version.py",
            r'__version__\s*=\s*"([^"]+)"',
            "platform runtime",
        ),
        "python": tomllib.loads(
            (ROOT / "agentmem/sdk/python/pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"],
        "python_lock": _uv_package_version(
            ROOT / "agentmem/sdk/python/uv.lock", "lians-sdk"
        ),
        "python_runtime": _match(
            ROOT / "agentmem/sdk/python/lians/client.py",
            r'SDK_VERSION\s*=\s*"([^"]+)"',
            "Python SDK runtime",
        ),
        "python_compat_private": tomllib.loads(
            (ROOT / "sdk/python/pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"],
        "python_compat_lock": _uv_package_version(
            ROOT / "sdk/python/uv.lock", "lians"
        ),
        "python_compat_runtime": _match(
            ROOT / "sdk/python/src/lians/client.py",
            r'SDK_VERSION\s*=\s*"([^"]+)"',
            "compatibility Python SDK runtime",
        ),
        "typescript": json.loads(
            (ROOT / "agentmem/sdk/typescript/package.json").read_text(encoding="utf-8")
        )["version"],
        "typescript_runtime": _match(
            ROOT / "agentmem/sdk/typescript/src/client.ts",
            r'export const VERSION\s*=\s*"([^"]+)"',
            "TypeScript SDK runtime",
        ),
        "typescript_lock": typescript_lock["version"],
        "typescript_lock_root": typescript_lock_root,
        "java": java_version,
        "java_runtime": _match(
            ROOT / "agentmem/sdk/java/src/main/java/ai/lians/LiansClient.java",
            r'public static final String VERSION\s*=\s*"([^"]+)"',
            "Java SDK runtime",
        ),
        "c": _match(
            ROOT / "agentmem/sdk/c/CMakeLists.txt",
            r"project\(lians_c_sdk VERSION ([^ )]+)",
            "C",
        ),
        "c_runtime": _match(
            ROOT / "agentmem/sdk/c/include/lians.h",
            r'#define LIANS_SDK_VERSION\s+"([^"]+)"',
            "C SDK runtime",
        ),
        "go": _match(
            ROOT / "agentmem/sdk/go/version.go",
            r'const Version = "([^"]+)"',
            "Go",
        ),
        "mcp": mcp_manifest["version"],
        "mcp_package": mcp_package_version,
        "helm_chart": _match(
            ROOT / "deploy/helm/lians/Chart.yaml",
            r"(?m)^version:\s*[\"']?([^\s\"']+)",
            "Helm chart",
        ),
        "helm_app": _match(
            ROOT / "deploy/helm/lians/Chart.yaml",
            r"(?m)^appVersion:\s*[\"']?([^\s\"']+)",
            "Helm app",
        ),
        "kustomize_app_label": _match(
            ROOT / "k8s/kustomization.yaml",
            r"(?m)^\s+app\.kubernetes\.io/version:\s*[\"']?([^\s\"']+)",
            "Kustomize application label",
        ),
        "npm_manual_publish_default": _match(
            ROOT / ".github/workflows/publish-lian-npm.yml",
            r"(?m)^\s+default:\s*([^\s#]+)",
            "npm manual-publish default",
        ),
        "mcp_manual_publish_default": _match(
            ROOT / ".github/workflows/publish-mcp-container.yml",
            r"(?m)^\s+default:\s*([^\s#]+)",
            "MCP manual-publish default",
        ),
        "ci_public_openapi": _match(
            ROOT / ".github/workflows/test.yml",
            r"specs/openapi/public-v([^\s/]+)\.json",
            "CI public OpenAPI",
        ),
        "ci_admin_openapi": _match(
            ROOT / ".github/workflows/test.yml",
            r"specs/openapi/admin-v([^\s/]+)\.json",
            "CI admin OpenAPI",
        ),
        "publishing_tag_example": _match(
            ROOT / "docs/publishing.md",
            r"git tag -a v([^\s]+)",
            "publishing tag example",
        ),
        "publishing_push_example": _match(
            ROOT / "docs/publishing.md",
            r"git push origin v([^\s]+)",
            "publishing push example",
        ),
        "publishing_go_example": _match(
            ROOT / "docs/publishing.md",
            r"agentmem/sdk/go@v([^\s]+)",
            "publishing Go example",
        ),
        "sdk_layout_install_example": _match(
            ROOT / "sdk/README.md",
            r"lians-sdk==([^\s]+)",
            "SDK layout install example",
        ),
        "root_java_install_example": _match(
            ROOT / "README.md",
            r"ai\.lians:lians-sdk:([^`\s]+)",
            "root Java install example",
        ),
        "java_maven_install_example": _match(
            ROOT / "agentmem/sdk/java/README.md",
            r"<version>([^<]+)</version>",
            "Java Maven install example",
        ),
        "java_gradle_install_example": _match(
            ROOT / "agentmem/sdk/java/README.md",
            r"implementation \"ai\.lians:lians-sdk:([^\"]+)\"",
            "Java Gradle install example",
        ),
        "citation": _match(
            ROOT / "CITATION.cff",
            r"(?m)^version:\s*([^\s]+)",
            "citation",
        ),
        "install_guide_lockstep": _match(
            ROOT / "docs/install.md",
            r"lock-step at `([^`]+)`",
            "install-guide lock-step statement",
        ),
        "glama_install_example": _match(
            ROOT / "docs/glama-deployment.md",
            r"`lians-sdk\[mcp\]` ([^\s]+) from",
            "Glama install example",
        ),
        "openapi_readme_release": _match(
            ROOT / "specs/openapi/README.md",
            r"For release `([^`]+)`",
            "OpenAPI README release",
        ),
        "llms_install_example": _match(
            ROOT / "llms-install.md",
            r"lians-sdk\[mcp\]==([^\"'\s]+)",
            "LLM install example",
        ),
    }


def verify_openapi_contract(version: str) -> None:
    for surface in ("public", "admin"):
        path = ROOT / f"specs/openapi/{surface}-v{version}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SystemExit(
                f"missing versioned {surface} OpenAPI contract: {path.relative_to(ROOT)}"
            ) from exc
        info = document.get("info", {})
        if document.get("openapi") != "3.1.0":
            raise SystemExit(f"{path.relative_to(ROOT)} must declare OpenAPI 3.1.0")
        if info.get("version") != version:
            raise SystemExit(
                f"{path.relative_to(ROOT)} declares info.version={info.get('version')!r}"
            )
        if info.get("x-lians-api-surface") != surface:
            raise SystemExit(
                f"{path.relative_to(ROOT)} has the wrong Lians API surface marker"
            )


def main() -> None:
    verify_python_distribution_contract()
    versions = declared_versions()
    expected = sys.argv[1].removeprefix("v") if len(sys.argv) > 1 else versions["platform"]
    if not SEMVER.fullmatch(expected):
        raise SystemExit(f"unsupported release version: {expected!r}")
    mismatches = {name: value for name, value in versions.items() if value != expected}
    if mismatches:
        raise SystemExit(
            f"release version {expected} disagrees with package metadata: {mismatches}"
        )
    mcpb_version, mcpb_sdk_pin = verify_mcpb_downstream_contract(expected)
    migration_head = verify_migration_contract()
    verify_openapi_contract(expected)
    print(
        f"all release surfaces declare {expected}; "
        f"single Alembic head is {migration_head}; downstream MCPB "
        f"{mcpb_version} pins published SDK {mcpb_sdk_pin}"
    )


if __name__ == "__main__":
    main()
