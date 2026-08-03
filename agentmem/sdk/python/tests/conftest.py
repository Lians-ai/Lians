"""Keep canonical SDK tests isolated from the server distribution."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]

# Local mode is deterministic and network-free in this package-level suite.
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["MASTER_ENCRYPTION_KEY"] = ""
os.environ["AGENTMEM_ALLOW_UNENCRYPTED"] = "true"
os.environ["RECALL_CACHE_ENABLED"] = "false"
os.environ["RLS_BARRIERS_ENABLED"] = "false"

lians = importlib.import_module("lians")

_IMPORTED_PACKAGE = Path(lians.__file__).resolve()
if not _IMPORTED_PACKAGE.is_relative_to(SDK_ROOT):
    raise RuntimeError(
        "canonical SDK tests imported the server distribution instead of "
        f"{SDK_ROOT}: {_IMPORTED_PACKAGE}"
    )
