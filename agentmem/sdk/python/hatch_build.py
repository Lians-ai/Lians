"""
Wheel build hook: vendor the Lians service engine as ``lians_engine``.

The engine source lives in one of two places depending on what we're
building from:

- **repo checkout** - ``../../src/lians`` relative to this file (the sdk dir)
- **unpacked sdist** - ``lians_engine/lians`` inside the project root (the
  sdist's own force-include put it there, because ``../../`` doesn't exist
  in an sdist)

A static ``[tool.hatch.build.targets.wheel.force-include]`` can only name one
path, and `python -m build` builds the wheel *from the sdist* - which is how
the 0.3.3 release pipeline failed with "Forced include not found". This hook
picks whichever source exists at build time.
"""
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class EngineVendorHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        sdist_engine = root / "lians_engine" / "lians"
        repo_engine = (root / ".." / ".." / "src" / "lians").resolve()

        if sdist_engine.is_dir():
            engine = sdist_engine
        elif repo_engine.is_dir():
            engine = repo_engine
        else:
            raise FileNotFoundError(
                "Lians engine source not found (looked for "
                f"{sdist_engine} and {repo_engine})"
            )

        build_data["force_include"][str(engine)] = "lians_engine/lians"

        # Same duality for the package __init__: the sdist already placed it
        # at lians_engine/__init__.py; the repo keeps it as _engine_init.py.
        sdist_init = root / "lians_engine" / "__init__.py"
        repo_init = root / "_engine_init.py"
        init = sdist_init if sdist_init.is_file() else repo_init
        build_data["force_include"][str(init)] = "lians_engine/__init__.py"
