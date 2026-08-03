# SDK source layout

The published Python SDK is the `lians-sdk` distribution built from
[`agentmem/sdk/python`](../agentmem/sdk/python). Its import name is `lians`:

```bash
python -m pip install --upgrade lians-sdk==0.5.0
```

```python
from lians import AsyncLiansClient, LiansClient
```

The deployable API wheel is the separate `lians-platform` distribution used
inside the isolated server image. It also owns a top-level `lians` import, so a
server environment must not install `lians-sdk`; clients belong in their own
application environment. Release CI enforces the distinct distribution names.

`sdk/python` is a source-only compatibility and API-conformance package. It is
kept for its typed async client and tests, but it is not a release artifact and
must not be uploaded to a package index. Its package metadata includes
`Private :: Do Not Upload` so PyPI rejects accidental publication.

Both projects provide the top-level `lians` import, so never install them in the
same environment. To migrate an environment that installed the old `lians`
distribution:

```bash
python -m pip uninstall lians
python -m pip install --upgrade lians-sdk==0.5.0
```

The old `LiansClient` is asynchronous. In `lians-sdk`, use
`AsyncLiansClient` for async code or `LiansClient` for synchronous code; method
names also differ in places (for example, `add_memory` becomes `add`). Migrate
calls explicitly rather than treating the packages as drop-in replacements.

Conformance tests remain runnable from an isolated development environment:

```bash
python -m pip install -e "./sdk/python[dev]"
python -m pytest sdk/python/tests
```

The compatibility client exposes the mediated Gate permit contract through
`evaluate_gate()` and `consume_gate_execution_permit()`. An allow response carries
the token once; evaluation get/list methods never return it. Use the canonical
identity of a separately credentialed broker and follow
[Gate execution permits](../docs/gate-execution-permits.md).
