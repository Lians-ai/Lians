# Conformance trust material — never production

This directory contains only raw Ed25519 **public** keys. The keys come from
published RFC 8032 test vectors and are globally known. Their corresponding
private values are not secrets and must never be used, loaded, or registered
in any Lians deployment, trust registry, signer, secret manager, or KMS.

`test-only-ed25519-public-key.base64` authenticates the signed fixture only.
`wrong-test-only-ed25519-public-key.base64` deliberately does not match it and
exists to prove that a cryptographically valid self-signature is not the same
thing as an authenticated issuer.

No private key is stored in this conformance package. A deployment must create
its own key in an approved signer and distribute its public trust anchor over a
separate authenticated channel.
