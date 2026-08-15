from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DEPLOY_WORKFLOW = (ROOT / ".github" / "workflows" / "fly-deploy.yml").read_text(
    encoding="utf-8"
)
MACHINE_SELECTOR = (ROOT / "scripts" / "select_fly_production_machine.py").read_text(
    encoding="utf-8"
)
SCHEMA_VERIFIER = (ROOT / "scripts" / "verify_production_schema.py").read_text(
    encoding="utf-8"
)
FLY_CONFIG = (ROOT / "fly.toml").read_text(encoding="utf-8")


def test_local_embedding_image_is_cpu_only_by_contract() -> None:
    """The CPU-only Fly runtime must not silently resolve CUDA PyPI wheels."""

    assert "ARG TORCH_CPU_VERSION=2.13.0+cpu" in DOCKERFILE
    assert (
        "ARG PYTORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu"
        in DOCKERFILE
    )
    assert '"torch==$TORCH_CPU_VERSION"' in DOCKERFILE
    assert "--constraint /tmp/torch-cpu-constraints.txt" in DOCKERFILE
    assert "python -m pip check" in DOCKERFILE

    # The build fails closed if the resolved environment is not the pinned CPU
    # wheel or if CUDA runtime distributions enter through a transitive update.
    assert "metadata.version('torch') == expected" in DOCKERFILE
    assert "torch.version.cuda is None" in DOCKERFILE
    assert "n.startswith(('nvidia-', 'cuda-'))" in DOCKERFILE
    assert "n == 'triton'" in DOCKERFILE


def test_runtime_artifacts_are_owned_during_copy() -> None:
    """Large layers get their final UID/GID without a recursive copy-up layer."""

    expected_copies = {
        "COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv",
        (
            "COPY --from=builder --chown=10001:10001 "
            "/app/.model_cache /app/.model_cache"
        ),
        "COPY --chown=10001:10001 agentmem/alembic /app/agentmem/alembic",
        (
            "COPY --chown=10001:10001 "
            "agentmem/alembic.ini /app/agentmem/alembic.ini"
        ),
    }
    lines = {line.strip() for line in DOCKERFILE.splitlines()}

    assert expected_copies <= lines
    assert "chown -R" not in DOCKERFILE
    assert "COPY --chown=10001:10001 agentmem/ /app/agentmem/" not in DOCKERFILE
    assert "USER 10001:10001" in DOCKERFILE
    assert "WORKDIR /app/agentmem" in DOCKERFILE
    # The ~1.3 GB embedding model is process-local; two Uvicorn workers would
    # duplicate it and exceed the production 2 GB Fly VM memory budget.
    assert '"--workers", "1"' in DOCKERFILE


def test_offline_model_and_deployment_identity_contracts_are_preserved() -> None:
    assert "ARG EXTRAS=local" in DOCKERFILE
    assert "ARG PREDOWNLOAD_MODEL=BAAI/bge-large-en-v1.5" in DOCKERFILE
    assert (
        "ARG PREDOWNLOAD_MODEL_REVISION="
        "d4aa6901d3a41ba39fb536a557fa166f842b0e09"
    ) in DOCKERFILE
    assert "SENTENCE_TRANSFORMERS_HOME=/app/.model_cache" in DOCKERFILE
    assert "TRANSFORMERS_OFFLINE=1" in DOCKERFILE
    assert "HF_DATASETS_OFFLINE=1" in DOCKERFILE
    assert (
        "SentenceTransformer('$PREDOWNLOAD_MODEL', "
        "revision='$PREDOWNLOAD_MODEL_REVISION')"
    ) in DOCKERFILE
    assert (
        'SENTENCE_TRANSFORMER_REVISION="${PREDOWNLOAD_MODEL_REVISION}"'
    ) in DOCKERFILE
    assert (
        'SENTENCE_TRANSFORMER_REVISION = "d4aa6901d3a41ba39fb536a557fa166f842b0e09"'
        in FLY_CONFIG
    )

    # The runtime build identity must remain late enough that changing a commit
    # SHA does not invalidate the expensive venv and offline-model layers.
    copy_index = DOCKERFILE.index(
        "COPY --from=builder --chown=10001:10001 /app/.model_cache"
    )
    build_arg_index = DOCKERFILE.index("ARG LIANS_BUILD_SHA=unknown")
    build_env_index = DOCKERFILE.index('ENV LIANS_BUILD_SHA="${LIANS_BUILD_SHA}"')
    workdir_index = DOCKERFILE.index("WORKDIR /app/agentmem")
    user_index = DOCKERFILE.index("USER 10001:10001")
    assert copy_index < build_arg_index < build_env_index < workdir_index < user_index

    # Production post-deploy verification must keep targeting the isolated
    # venv and exact GitHub commit embedded in both the image environment and
    # Fly image metadata.
    assert "scripts/verify_production_schema.py" in DEPLOY_WORKFLOW
    assert "/opt/venv/bin/alembic" in SCHEMA_VERIFIER
    assert '--build-arg "LIANS_BUILD_SHA=$GITHUB_SHA"' in DEPLOY_WORKFLOW
    assert '--expected-sha "$GITHUB_SHA"' in DEPLOY_WORKFLOW
    assert '--expected-build-sha "$GITHUB_SHA"' in DEPLOY_WORKFLOW
    assert 'labels.get("GH_SHA") != expected_sha' in MACHINE_SELECTOR
