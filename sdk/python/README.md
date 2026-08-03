# Lians source-only compatibility client

This directory contains the private `lians` compatibility distribution used by
the repository's API-conformance tests. It is not a release artifact and its
`Private :: Do Not Upload` classifier is enforced by the release checker.

For supported Python applications, install the canonical SDK instead:

```bash
pip install lians-sdk
```

Do not install this compatibility package alongside `lians-sdk`: both expose
the same top-level `lians` import namespace.
