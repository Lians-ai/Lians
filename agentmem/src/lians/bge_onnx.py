"""Pinned, air-gap-safe artifact handling for the exact BGE ONNX runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BGE_ONNX_MODEL_REPOSITORY = "BAAI/bge-large-en-v1.5"
BGE_ONNX_MODEL_REVISION = "d4aa6901d3a41ba39fb536a557fa166f842b0e09"
BGE_ONNX_MODEL_SHA256 = "69ed3f810d3b6d13f70dff9ca89966f39c0a0e877fb88211be7bcc070df2a2ce"
BGE_ONNX_MODEL_BYTES = 1_336_854_281
BGE_ONNX_TOKENIZER_SHA256 = "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"
BGE_ONNX_TOKENIZER_BYTES = 711_396
BGE_ONNX_MANIFEST_SHA256 = "6aeb7fed1b1c9de0e86770b8cbbaaf8547f6bda593ec12dd94aff80aaafa0462"
BGE_ONNX_EMBEDDING_DIMENSION = 1024
BGE_ONNX_MAX_SEQUENCE_LENGTH = 512
BGE_ONNX_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class BgeOnnxArtifactError(RuntimeError):
    """The configured artifact is absent, changed, or not the pinned export."""


@dataclass(frozen=True)
class BgeOnnxArtifactSpec:
    model_sha256: str = BGE_ONNX_MODEL_SHA256
    model_bytes: int = BGE_ONNX_MODEL_BYTES
    tokenizer_sha256: str = BGE_ONNX_TOKENIZER_SHA256
    tokenizer_bytes: int = BGE_ONNX_TOKENIZER_BYTES
    manifest_sha256: str = BGE_ONNX_MANIFEST_SHA256

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "lians.bge-onnx-artifact.v1",
            "model": {
                "repository": BGE_ONNX_MODEL_REPOSITORY,
                "revision": BGE_ONNX_MODEL_REVISION,
                "file": "model.onnx",
                "sha256": self.model_sha256,
                "bytes": self.model_bytes,
                "embedding_dimension": BGE_ONNX_EMBEDDING_DIMENSION,
                "pooling": "cls",
                "document_semantics": "raw_l2_normalized",
            },
            "tokenizer": {
                "file": "tokenizer.json",
                "sha256": self.tokenizer_sha256,
                "bytes": self.tokenizer_bytes,
                "max_sequence_length": BGE_ONNX_MAX_SEQUENCE_LENGTH,
            },
            "query_semantics": {
                "instruction": BGE_ONNX_QUERY_INSTRUCTION,
                "combine": "l2_normalize_each_then_mean_then_l2_normalize",
            },
            "runtime": {
                "provider": "CPUExecutionProvider",
                "disable_prepacking": True,
                "graph_optimization": "all",
            },
        }

    def manifest_bytes(self) -> bytes:
        return (json.dumps(self.manifest(), indent=2, sort_keys=True) + "\n").encode()


PINNED_BGE_ONNX_SPEC = BgeOnnxArtifactSpec()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True)
class ValidatedBgeOnnxArtifact:
    root: Path
    model: Path
    tokenizer: Path
    manifest: Path
    model_signature: tuple[int, int, int, int]
    tokenizer_signature: tuple[int, int, int, int]
    manifest_signature: tuple[int, int, int, int]

    def assert_unchanged(self) -> None:
        expected = {
            self.model: self.model_signature,
            self.tokenizer: self.tokenizer_signature,
            self.manifest: self.manifest_signature,
        }
        for path, signature in expected.items():
            try:
                actual = _signature(path)
            except OSError as exc:
                raise BgeOnnxArtifactError(
                    f"BGE ONNX artifact changed during load: {path.name}"
                ) from exc
            if actual != signature:
                raise BgeOnnxArtifactError(f"BGE ONNX artifact changed during load: {path.name}")


def _require_regular_child(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise BgeOnnxArtifactError(f"BGE ONNX artifact requires a regular local {name!r} file")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BgeOnnxArtifactError(f"BGE ONNX artifact cannot read {name!r}") from exc
    if resolved.parent != root:
        raise BgeOnnxArtifactError(f"BGE ONNX artifact {name!r} escapes its directory")
    return resolved


def _require_size_and_hash(
    path: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    actual_bytes = path.stat(follow_symlinks=False).st_size
    if actual_bytes != expected_bytes:
        raise BgeOnnxArtifactError(
            f"BGE ONNX artifact size mismatch for {path.name}: "
            f"expected {expected_bytes}, got {actual_bytes}"
        )
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise BgeOnnxArtifactError(f"BGE ONNX artifact SHA-256 mismatch for {path.name}")


def validate_bge_onnx_artifact(
    artifact_dir: str | os.PathLike[str],
    *,
    spec: BgeOnnxArtifactSpec = PINNED_BGE_ONNX_SPEC,
) -> ValidatedBgeOnnxArtifact:
    """Validate the exact pinned manifest, model, and tokenizer without network IO."""

    raw_root = Path(artifact_dir).expanduser()
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise BgeOnnxArtifactError("BGE_ONNX_ARTIFACT_DIR must be a regular local directory")
    root = raw_root.resolve(strict=True)
    model = _require_regular_child(root, "model.onnx")
    tokenizer = _require_regular_child(root, "tokenizer.json")
    manifest = _require_regular_child(root, "manifest.json")

    manifest_bytes = manifest.read_bytes()
    expected_manifest = spec.manifest_bytes()
    expected_manifest_sha256 = hashlib.sha256(expected_manifest).hexdigest()
    if expected_manifest_sha256 != spec.manifest_sha256:
        raise BgeOnnxArtifactError("internal pinned BGE ONNX manifest hash mismatch")
    if hashlib.sha256(manifest_bytes).hexdigest() != spec.manifest_sha256:
        raise BgeOnnxArtifactError("BGE ONNX export manifest SHA-256 mismatch")
    if manifest_bytes != expected_manifest:
        raise BgeOnnxArtifactError("BGE ONNX export manifest content mismatch")

    _require_size_and_hash(
        model,
        expected_bytes=spec.model_bytes,
        expected_sha256=spec.model_sha256,
    )
    _require_size_and_hash(
        tokenizer,
        expected_bytes=spec.tokenizer_bytes,
        expected_sha256=spec.tokenizer_sha256,
    )
    return ValidatedBgeOnnxArtifact(
        root=root,
        model=model,
        tokenizer=tokenizer,
        manifest=manifest,
        model_signature=_signature(model),
        tokenizer_signature=_signature(tokenizer),
        manifest_signature=_signature(manifest),
    )


def export_bge_onnx_artifact(
    *,
    model_path: str | os.PathLike[str],
    tokenizer_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    spec: BgeOnnxArtifactSpec = PINNED_BGE_ONNX_SPEC,
) -> ValidatedBgeOnnxArtifact:
    """Copy verified upstream files into one immutable-shape local artifact directory."""

    raw_model_source = Path(model_path).expanduser()
    raw_tokenizer_source = Path(tokenizer_path).expanduser()
    if not raw_model_source.is_file():
        raise BgeOnnxArtifactError("--model must name a local file")
    if not raw_tokenizer_source.is_file():
        raise BgeOnnxArtifactError("--tokenizer must name a local file")
    # Upstream download caches commonly expose verified blobs through symlinks.
    # They are safe as exporter inputs because the destination is a new regular
    # file and is rehashed after the copy. Runtime artifacts remain symlink-free.
    model_source = raw_model_source.resolve(strict=True)
    tokenizer_source = raw_tokenizer_source.resolve(strict=True)
    _require_size_and_hash(
        model_source,
        expected_bytes=spec.model_bytes,
        expected_sha256=spec.model_sha256,
    )
    _require_size_and_hash(
        tokenizer_source,
        expected_bytes=spec.tokenizer_bytes,
        expected_sha256=spec.tokenizer_sha256,
    )

    requested = Path(output_dir).expanduser()
    parent = requested.parent.resolve(strict=True)
    target = parent / requested.name
    if not requested.name or target.exists() or target.is_symlink():
        raise BgeOnnxArtifactError("--output must be a new directory")

    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=parent))
    completed = False
    try:
        shutil.copyfile(model_source, staging / "model.onnx")
        shutil.copyfile(tokenizer_source, staging / "tokenizer.json")
        (staging / "manifest.json").write_bytes(spec.manifest_bytes())
        validate_bge_onnx_artifact(staging, spec=spec)
        os.replace(staging, target)
        completed = True
        return validate_bge_onnx_artifact(target, spec=spec)
    finally:
        if not completed:
            shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage the exact pinned BAAI/bge-large-en-v1.5 ONNX artifact."
    )
    parser.add_argument("--model", required=True, help="Local upstream onnx/model.onnx")
    parser.add_argument("--tokenizer", required=True, help="Local upstream tokenizer.json")
    parser.add_argument("--output", required=True, help="New destination directory")
    args = parser.parse_args(argv)
    artifact = export_bge_onnx_artifact(
        model_path=args.model,
        tokenizer_path=args.tokenizer,
        output_dir=args.output,
    )
    print(
        json.dumps(
            {
                "artifact_dir": str(artifact.root),
                "manifest_sha256": BGE_ONNX_MANIFEST_SHA256,
                "model_sha256": BGE_ONNX_MODEL_SHA256,
                "tokenizer_sha256": BGE_ONNX_TOKENIZER_SHA256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
