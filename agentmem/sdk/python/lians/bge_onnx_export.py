"""Installed CLI wrapper for the pinned BGE ONNX artifact exporter."""

from __future__ import annotations

from .local_client import _ensure_src_importable


def main() -> int:
    _ensure_src_importable()
    from src.lians.bge_onnx import main as engine_main

    return engine_main()


if __name__ == "__main__":
    raise SystemExit(main())
