"""
lians_engine - the Lians service engine, vendored into the lians-sdk wheel.

This package exists so ``LocalLiansClient`` (zero-setup local mode) works from
a plain ``pip install lians-sdk[local]``, outside the monorepo. It is the
same code as ``agentmem/src/lians`` in the repository, included at build time
(see ``[tool.hatch.build.targets.wheel.force-include]`` in pyproject.toml).

Not a public API: import ``lians`` instead. The SDK aliases this package to
``src.lians`` at import time for the service layer's benefit.
"""
