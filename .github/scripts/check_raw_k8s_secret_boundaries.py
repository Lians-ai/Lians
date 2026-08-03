#!/usr/bin/env python3
"""Fail if raw Kubernetes examples weaken credential or egress boundaries."""

from __future__ import annotations

import itertools
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
K8S = ROOT / "k8s"

SECRET_SHAPES = {
    "secret.yaml": ("agentmem-database-runtime", {"DATABASE_URL"}),
    "application-secret.yaml": (
        "agentmem-application",
        {
            "SUBJECT_REFERENCE_KEY",
            "METRICS_BEARER_TOKEN",
            "VOYAGE_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "STRIPE_API_KEY",
        },
    ),
    "redis-secret.yaml": ("agentmem-redis", {"REDIS_URL"}),
    "receipt-secret.yaml": (
        "agentmem-receipt-signing",
        {"RECEIPT_SIGNING_PRIVATE_KEY"},
    ),
    "kms-secret.yaml": (
        "agentmem-kms",
        {
            "KMS_AWS_REGION",
            "KMS_AWS_ENCRYPTED_KEY",
            "KMS_AWS_KEY_ID",
            "KMS_AWS_PREVIOUS_REGION",
            "KMS_AWS_PREVIOUS_ENCRYPTED_KEY",
            "KMS_AWS_PREVIOUS_KEY_ID",
            "KMS_AZURE_VAULT_URL",
            "KMS_AZURE_SECRET_NAME",
            "KMS_AZURE_PREVIOUS_VAULT_URL",
            "KMS_AZURE_PREVIOUS_SECRET_NAME",
            "KMS_VAULT_ADDR",
            "KMS_VAULT_TOKEN",
            "KMS_VAULT_PATH",
            "KMS_VAULT_MOUNT_POINT",
            "KMS_VAULT_PREVIOUS_ADDR",
            "KMS_VAULT_PREVIOUS_PATH",
            "KMS_VAULT_PREVIOUS_MOUNT_POINT",
        },
    ),
    "recorder-secret.yaml": (
        "agentmem-recorder-ingest",
        {"LIANS_INGEST_API_KEY"},
    ),
}
WORKER_PREFIXES = (
    "IMPACT_ASSESSMENT_WORKER",
    "RECORDER_EVIDENCE_INDEX_WORKER",
    "SUBJECT_ERASURE_WORKER",
    "SCIM_RECONCILIATION_WORKER",
)
WORKER_FIELDS = (
    "ENABLED",
    "POLL_SECONDS",
    "BATCH_SIZE",
    "CONCURRENCY",
    "LEASE_SECONDS",
    "PAGE_SIZE",
    "MAX_PAGES_PER_CLAIM",
    "RETRY_BASE_SECONDS",
    "RETRY_MAX_SECONDS",
    "MAX_ATTEMPTS",
)


def _documents(path: pathlib.Path) -> list[dict]:
    documents = [
        value
        for value in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if value is not None
    ]
    if not documents or any(not isinstance(value, dict) for value in documents):
        raise SystemExit(f"{path.relative_to(ROOT)} must contain YAML mappings")
    return documents


def _document(name: str) -> dict:
    documents = _documents(K8S / name)
    if len(documents) != 1:
        raise SystemExit(f"k8s/{name} must contain exactly one YAML document")
    return documents[0]


def _secret_keys(document: dict) -> set[str]:
    if document.get("kind") != "Secret":
        raise SystemExit("expected a Kubernetes Secret")
    values = document.get("stringData")
    if not isinstance(values, dict):
        raise SystemExit("reference Secrets must use an explicit stringData mapping")
    return set(values)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _secret_references(document: dict) -> set[str]:
    references: set[str] = set()
    for mapping in _walk(document):
        for field in ("secretRef", "secretKeyRef"):
            reference = mapping.get(field)
            if isinstance(reference, dict) and isinstance(reference.get("name"), str):
                references.add(reference["name"])
    return references


def _container_secret_projections(container: dict) -> dict[str, tuple[str, str]]:
    for source in container.get("envFrom") or []:
        if isinstance(source, dict) and source.get("secretRef"):
            raise SystemExit(
                f"container {container.get('name')} must not import a complete Secret"
            )
    projections: dict[str, tuple[str, str]] = {}
    for item in container.get("env") or []:
        if not isinstance(item, dict):
            continue
        reference = item.get("valueFrom", {}).get("secretKeyRef")
        if not isinstance(reference, dict):
            continue
        env_name = item.get("name")
        secret_name = reference.get("name")
        secret_key = reference.get("key")
        if not all(isinstance(value, str) for value in (env_name, secret_name, secret_key)):
            raise SystemExit("secretKeyRef projections must name the env, Secret, and key")
        projections[env_name] = (secret_name, secret_key)
    return projections


def _check_network_policies() -> None:
    paths = (
        K8S / "default-deny-networkpolicy.yaml",
        K8S / "networkpolicy.yaml",
        K8S / "otel-collector.yaml",
        K8S / "backup" / "backup-networkpolicy.yaml",
        ROOT / "deploy" / "gate-mediator" / "networkpolicy.yaml",
    )
    for path in paths:
        for document in _documents(path):
            if document.get("kind") != "NetworkPolicy":
                continue
            name = document.get("metadata", {}).get("name", "<unnamed>")
            for index, rule in enumerate(document.get("spec", {}).get("egress") or []):
                peers = rule.get("to") if isinstance(rule, dict) else None
                if not isinstance(peers, list) or not peers:
                    raise SystemExit(
                        f"NetworkPolicy {name} egress rule {index} has no destination selector"
                    )
                for peer in peers:
                    cidr = peer.get("ipBlock", {}).get("cidr")
                    if cidr in {"0.0.0.0/0", "::/0"}:
                        raise SystemExit(
                            f"NetworkPolicy {name} contains world-open egress {cidr}"
                        )


def main() -> None:
    secret_documents: dict[str, dict] = {}
    for filename, (expected_name, expected_keys) in SECRET_SHAPES.items():
        document = _document(filename)
        actual_name = document.get("metadata", {}).get("name")
        if actual_name != expected_name:
            raise SystemExit(
                f"k8s/{filename} must define Secret {expected_name}, got {actual_name}"
            )
        actual_keys = _secret_keys(document)
        if actual_keys != expected_keys:
            raise SystemExit(
                f"k8s/{filename} has unexpected keys: "
                f"expected {sorted(expected_keys)}, got {sorted(actual_keys)}"
            )
        secret_documents[filename] = document

    # A duplicated key makes future broad projections or external-secret rules
    # ambiguous, even when today's Deployment uses individual secretKeyRefs.
    for left, right in itertools.combinations(secret_documents, 2):
        overlap = _secret_keys(secret_documents[left]) & _secret_keys(
            secret_documents[right]
        )
        if overlap:
            raise SystemExit(
                f"least-privilege Secrets {left} and {right} overlap: {sorted(overlap)}"
            )

    migration = _document("migration-secret.yaml")
    admin = _document("admin-secret.yaml")
    if _secret_keys(migration) != {"DATABASE_URL"}:
        raise SystemExit("the migration Secret must contain only DATABASE_URL")
    if _secret_keys(admin) != {"ADMIN_SECRET"}:
        raise SystemExit("the admin Secret must contain only ADMIN_SECRET")

    deployment = _document("deployment.yaml")
    migration_job = _document("migrate-job.yaml")
    kustomization = _document("kustomization.yaml")
    pod_spec = deployment.get("spec", {}).get("template", {}).get("spec", {})
    if (
        pod_spec.get("serviceAccountName") != "agentmem-api"
        or pod_spec.get("automountServiceAccountToken") is not False
    ):
        raise SystemExit("public Deployment requires the tokenless agentmem-api identity")

    expected_resources = set(SECRET_SHAPES) | {
        "serviceaccounts.yaml",
        "default-deny-networkpolicy.yaml",
    }
    privileged_resources = {
        "migration-secret.yaml",
        "admin-secret.yaml",
        "migrate-job.yaml",
    }
    resources = set(kustomization.get("resources") or [])
    missing = sorted(expected_resources - resources)
    if missing:
        raise SystemExit(f"least-privilege resources missing from Kustomize: {missing}")
    included = sorted(resources & privileged_resources)
    if included:
        raise SystemExit(f"privileged resources entered Kustomize: {included}")

    expected_deployment_refs = {
        expected_name for expected_name, _ in SECRET_SHAPES.values()
    } - {"agentmem-recorder-ingest"}
    actual_deployment_refs = _secret_references(deployment)
    if actual_deployment_refs != expected_deployment_refs:
        raise SystemExit(
            "public Deployment secret references are not least-privilege: "
            f"expected {sorted(expected_deployment_refs)}, "
            f"got {sorted(actual_deployment_refs)}"
        )

    migration_wait = next(
        (
            container
            for container in pod_spec.get("initContainers", [])
            if container.get("name") == "wait-for-migrations"
        ),
        None,
    )
    if not isinstance(migration_wait, dict):
        raise SystemExit("public Deployment is missing wait-for-migrations")
    wait_projections = _container_secret_projections(migration_wait)
    if wait_projections != {
        "DATABASE_URL": ("agentmem-database-runtime", "DATABASE_URL")
    }:
        raise SystemExit("migration-wait init has an unsafe database projection")
    wait_command = "\n".join(str(value) for value in migration_wait.get("command") or [])
    if "python -m lians.migration_contract" not in wait_command:
        raise SystemExit("migration-wait init must use the packaged schema contract")

    api_container = next(
        (
            container
            for container in pod_spec.get("containers", [])
            if container.get("name") == "agentmem"
        ),
        None,
    )
    if not isinstance(api_container, dict):
        raise SystemExit("public Deployment is missing the agentmem container")
    api_projections = _container_secret_projections(api_container)
    expected_projection_names = {
        key
        for filename, (_, keys) in SECRET_SHAPES.items()
        if filename != "recorder-secret.yaml"
        for key in keys
    }
    if set(api_projections) != expected_projection_names:
        raise SystemExit(
            "public Deployment must project every runtime key explicitly and only once"
        )
    for env_name, (secret_name, secret_key) in api_projections.items():
        if env_name != secret_key:
            raise SystemExit(f"{env_name} must project its same-named Secret key")
        expected_secret = next(
            name
            for _, (name, keys) in SECRET_SHAPES.items()
            if secret_key in keys
        )
        if secret_name != expected_secret:
            raise SystemExit(
                f"{env_name} must be sourced from {expected_secret}, got {secret_name}"
            )

    migration_refs = _secret_references(migration_job)
    if migration_refs != {"agentmem-migration-secret"}:
        raise SystemExit("migration Job must reference only agentmem-migration-secret")
    migration_pod = migration_job.get("spec", {}).get("template", {}).get("spec", {})
    if (
        migration_pod.get("serviceAccountName") != "agentmem-migrator"
        or migration_pod.get("automountServiceAccountToken") is not False
    ):
        raise SystemExit("migration Job requires the tokenless migrator identity")
    migration_container = migration_pod.get("containers", [{}])[0]
    migration_environment = {
        item.get("name"): item.get("value")
        for item in migration_container.get("env") or []
        if isinstance(item, dict) and "value" in item
    }
    expected_migration_environment = {
        "DEPLOYMENT_ENVIRONMENT": "production",
        "PRODUCTION_ALLOW_LOCAL_DATA_SERVICE_SOCKETS": "false",
        "MIGRATION_STATEMENT_TIMEOUT_MS": "1500000",
        "MIGRATION_LOCK_TIMEOUT_MS": "5000",
        "MIGRATION_IDLE_TRANSACTION_TIMEOUT_MS": "300000",
    }
    for key, expected in expected_migration_environment.items():
        if migration_environment.get(key) != expected:
            raise SystemExit(f"migration Job requires {key}={expected}")
    deadline = migration_job.get("spec", {}).get("activeDeadlineSeconds")
    if not isinstance(deadline, int) or deadline < 60:
        raise SystemExit("migration Job requires a bounded active deadline")
    if int(migration_environment["MIGRATION_STATEMENT_TIMEOUT_MS"]) > (
        deadline - 60
    ) * 1000:
        raise SystemExit("migration statement timeout exceeds the Job deadline budget")
    command_text = (K8S / "migrate-job.yaml").read_text(encoding="utf-8")
    for required in (
        "python -m lians.migration_preflight",
        "alembic -c alembic.ini upgrade head",
        "python -m lians.migration_contract",
    ):
        if required not in command_text:
            raise SystemExit(f"migration Job is missing required command: {required}")

    collector_documents = _documents(K8S / "otel-collector.yaml")
    collector_refs = set().union(
        *(_secret_references(document) for document in collector_documents)
    )
    if collector_refs != {"agentmem-recorder-ingest"}:
        raise SystemExit("Recorder collector must reference only its namespace ingest key")
    collector = next(
        document
        for document in collector_documents
        if document.get("kind") == "StatefulSet"
    )
    collector_pod = collector.get("spec", {}).get("template", {}).get("spec", {})
    if (
        collector_pod.get("serviceAccountName") != "lians-otel-collector"
        or collector_pod.get("automountServiceAccountToken") is not False
    ):
        raise SystemExit("Recorder collector requires its tokenless ServiceAccount")

    service_accounts = {
        document.get("metadata", {}).get("name")
        for document in _documents(K8S / "serviceaccounts.yaml")
        if document.get("kind") == "ServiceAccount"
        and document.get("automountServiceAccountToken") is False
    }
    if service_accounts != {
        "agentmem-api",
        "agentmem-migrator",
        "lians-otel-collector",
    }:
        raise SystemExit("raw workloads require dedicated tokenless ServiceAccounts")

    default_deny = _document("default-deny-networkpolicy.yaml")
    default_spec = default_deny.get("spec", {})
    if (
        default_deny.get("kind") != "NetworkPolicy"
        or default_spec.get("podSelector") != {}
        or set(default_spec.get("policyTypes") or []) != {"Ingress", "Egress"}
        or "ingress" in default_spec
        or "egress" in default_spec
    ):
        raise SystemExit("raw namespace default-deny NetworkPolicy is incomplete")

    config = _document("configmap.yaml").get("data", {})
    required_fail_closed_config = {
        "API_SURFACE": "public",
        "PRODUCTION_ALLOW_LOCAL_DATA_SERVICE_SOCKETS": "false",
        "RATE_LIMIT_BACKEND_FAILURE_MODE": "deny",
        "IMPACT_ASSESSMENT_WORKER_ENABLED": "true",
        "RECORDER_EVIDENCE_INDEX_WORKER_ENABLED": "true",
        "SUBJECT_ERASURE_WORKER_ENABLED": "true",
        "SCIM_RECONCILIATION_WORKER_ENABLED": "true",
    }
    for key, expected in required_fail_closed_config.items():
        if config.get(key) != expected:
            raise SystemExit(f"raw ConfigMap requires {key}={expected}")
    for prefix in WORKER_PREFIXES:
        missing = [
            f"{prefix}_{field}"
            for field in WORKER_FIELDS
            if f"{prefix}_{field}" not in config
        ]
        if missing:
            raise SystemExit(f"raw ConfigMap is missing worker settings: {missing}")
    if config.get("KMS_PROVIDER") == "env":
        raise SystemExit("raw production ConfigMap must select an external KMS")
    if "lians-otel-collector" in config.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""):
        raise SystemExit("API telemetry must not recurse through the Recorder gateway")

    _check_network_policies()
    print("raw Kubernetes secret, identity, and egress boundaries are isolated")


if __name__ == "__main__":
    main()
