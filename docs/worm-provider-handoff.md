# Verified WORM provider handoff

`prepare_worm_handoff.py` produces a sealed request, not proof of immutable
storage. `upload_worm_handoff.py` is the only supported promotion path from that
pending request to `provider_verified_immutable`. It re-verifies the local bundle
and the handoff sidecar, uploads every object create-only, verifies the exact
object version or generation, enforces locked retention and the requested hold,
then emits a schema-validated core attestation and SHA-256 sidecar without
overwriting an existing local result. It also uploads the exact canonical core
attestation bytes create-only under a deterministic, digest-derived object name
inside the same locked and versioned destination prefix. The resulting local
anchor record captures that exact provider object ID, provider checksums,
effective retention, and hold state. Its own SHA-256 sidecar completes the
four-file result.

The provider adapters contain no object/version delete, overwrite,
retention-shortening, or hold-clearing operation. Local crash-staging files are
removed only after the exact four-file result and provider anchor reverify. A
rerun safely reuses a completed object only when its bytes, size,
provider checksum, immutable object ID, locked retention, and hold state verify.
An interrupted run can therefore resume at the next object without replacing a
completed object. S3 multipart uploads interrupted before completion are left for
a separately administered bucket lifecycle rule; the uploader identity must not
receive `s3:AbortMultipartUpload` or object/version deletion permission.

## Runtime image and dependencies

Build the all-provider backup image from the backup directory, then pin the
resulting registry digest in production:

```bash
docker build \
  --file ops/backup/Dockerfile.worm \
  --tag registry.example/lians-backup-worm:0.1.0 \
  ops/backup
```

`requirements-worm.txt` is the reviewed input policy; the production image
installs its exact transitive resolution from `requirements-worm.lock` with
artifact hashes required. Regenerate that lock only in a dependency-update PR.
The direct dependency policy is:

- common: `jsonschema[format-nongpl]>=4.23.0,<5`;
- AWS: `boto3>=1.35.20,<2` (and its matching `botocore` dependency);
- Google Cloud: `google-auth>=2.35.0,<3`,
  `google-cloud-storage>=3.4.0,<4`, and `google-crc32c>=1.6.0,<2`; and
- Azure: `azure-identity>=1.19.0,<2`, `azure-mgmt-storage>=23.1.0,<25`,
  and `azure-storage-blob>=12.26.0,<13`.

Host installations may install the common dependency and only one provider set;
provider imports are lazy. Production builds use the committed hash-pinned lock,
generate an SBOM, scan it, and publish the image by digest. No cloud CLI is
required.

## Identity contract

All credentials come from the official SDK default credential chain. There are
no credential CLI options. Prefer Kubernetes workload identity (IRSA, GKE
Workload Identity Federation, or Azure Workload Identity); do not put access
keys, service-account JSON, client secrets, SAS tokens, signed URLs, or storage
connection strings in arguments, destination URIs, logs, handoffs, or
attestations.

Every run requires `LIANS_WORM_VERIFIER_IDENTITY`, set to the stable platform
identity that owns the verification action, for example a Kubernetes service
account URI. It also requires an explicit provider ownership boundary:

| Destination | Required non-secret identity environment |
|---|---|
| `s3://BUCKET/PREFIX` | `LIANS_WORM_AWS_ACCOUNT_ID` (12 digits) |
| `gs://BUCKET/PREFIX` | `LIANS_WORM_GCP_PROJECT_ID` and `LIANS_WORM_GCP_PROJECT_NUMBER` |
| `azure://ACCOUNT/CONTAINER/PREFIX` | `LIANS_WORM_AZURE_TENANT_ID`, `LIANS_WORM_AZURE_SUBSCRIPTION_ID`, `LIANS_WORM_AZURE_RESOURCE_GROUP`, and `LIANS_WORM_AZURE_STORAGE_ACCOUNT` |

The uploader verifies AWS identity with STS, GCS bucket ownership with the
provider project number, and Azure storage ownership with the ARM resource ID.
The effective provider principal, SDK versions, resource identity, and an
immutable-policy revision digest are retained in the attestation.

## Provider prerequisites and least privilege

Pre-provision the immutable boundary. The uploader never creates buckets,
containers, accounts, versioning settings, or retention capabilities.

For S3, enable versioning and Object Lock when the bucket is created. Permit only
STS identity lookup; bucket versioning, location, and Object Lock reads; exact
prefix object/version create and read; multipart create/upload/complete; and
object retention/legal-hold writes. Require the expected bucket owner and deny
governance bypass, retention shortening, hold clearing, object deletion, version
deletion, bucket policy changes, and Object Lock changes. Configure a storage-admin
lifecycle rule for abandoned multipart uploads.

For GCS, enable Object Retention Lock and object versioning on the bucket. The
custom role needs bucket metadata read and exact-prefix `storage.objects.create`,
`storage.objects.get`, `storage.objects.update`,
`storage.objects.setRetention`, and
`storage.objects.overrideUnlockedRetention`. Do not grant object delete, bucket
delete, IAM administration, or retention-feature administration. The uploader
uses a generation-match-zero precondition and locks each generation's retention;
a requested legal hold is represented by a temporary hold and is verified after
the lock.

For Azure, enable blob versioning and version-level immutability on the target
container. Grant ARM read access to the named storage account plus exact-container
data actions for blob create/read, setting version immutability, and setting a
legal hold. Use a custom role that omits blob/version/container deletion, policy
deletion, hold clearing, account key listing, and Shared Key administration. The
uploader supports the public Azure endpoint and authenticates with
`DefaultAzureCredential` with interactive browser authentication disabled.

Provider controls and API behavior are defined in the official documentation for
[S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html),
[GCS Object Retention Lock](https://cloud.google.com/storage/docs/object-lock), and
[Azure version-level immutability](https://learn.microsoft.com/azure/storage/blobs/immutable-policy-configure-version-scope).

## Invocation

The sealed handoff must remain beside its `.sha256` sidecar. On a first run, the
four derived local output paths must be absent. A rerun accepts only exact,
schema-valid crash-staging or published artifacts for the same sealed handoff and
bundle; it never replaces a different existing path:

```bash
/opt/lians-worm-venv/bin/python /opt/lians-backup/upload_worm_handoff.py \
  /backup/handoffs/lians-20260802t020000z.json \
  --bundle /backup/bundles/lians-20260802t020000z \
  --output /backup/handoffs/lians-20260802t020000z.provider-attestation.json
```

The command exits nonzero and produces no complete four-file result if any object
is absent, different, unversioned, missing a provider checksum, not locked through
at least the requested time, or missing a requested hold. GCS and Azure always
stream the immutable generation/version back through SHA-256 because their stored
provider checksums use another algorithm. S3 uses a provider full-object SHA-256
when it is available and equal; multipart/composite, absent, or conflicting
SHA-256 metadata causes a streamed verification, with any true checksum conflict
still failing closed.

Before uploading the first bundle object, the uploader also checks every source
name and the longer synthetic anchor name against the selected provider's object
name/key budget. Choose a shorter destination prefix if that preflight fails.

The core and anchor record validate against
`ops/backup/schemas/worm-provider-attestation-v1.schema.json` and
`ops/backup/schemas/worm-provider-attestation-anchor-v1.schema.json`. The anchor
object name is
`lians-provider-attestation-<backup-id>-<core-sha256>.json`; its digest-derived
name is collision-safe inside the handoff's existing locked/versioned prefix.
The anchor record cross-binds the core digest and canonical provider-boundary
digest. It deliberately does not contain a hash of itself; its separate sidecar
hashes the canonical anchor-record bytes and avoids a circular self-hash.

Given a core attestation path, `verify_worm_attestation.py` revalidates the core
and anchor JSON documents, both SHA-256 sidecars, and the exact immutable provider
object ID/version/generation. It executes only provider reads and uses the
provider SDK default workload identity plus the expected ownership environment
from the identity contract above; it has no credential options:

```bash
/opt/lians-worm-venv/bin/python /opt/lians-backup/verify_worm_attestation.py \
  /backup/handoffs/lians-20260802t020000z.provider-attestation.json
```

The four local outputs are the core attestation, `<core>.sha256`,
`<core>.anchor.json`, and `<core>.anchor.json.sha256`. The provider's immutable
copy of the exact canonical core bytes is the authenticity and integrity anchor
within the configured provider trust boundary; no application signing secret is
introduced. This does not replace provider audit logs, independently governed
IAM, retention administration, or control-plane monitoring. Retain all four
local files in the recovery evidence boundary so the provider anchor can be
rechecked later.

## Exact Kubernetes CronJob integration hooks

The reference logical-backup CronJob applies the following hooks. Downstream
overlays must preserve them when changing the image, identity, or storage layout:

1. Build `ops/backup/Dockerfile.worm` and replace the container image with its
   immutable registry digest so `/opt/lians-worm-venv/bin/python` is available.
2. Bind the pod's dedicated service account to exactly one provider workload
   identity. Do not mount a static cloud credential Secret.
3. Inject `LIANS_WORM_VERIFIER_IDENTITY` and the provider-specific expected
   identity variables above from non-secret configuration. Keep database
   credentials isolated from the provider uploader wherever the platform can use
   separate containers/service accounts.
4. Immediately after `prepare_worm_handoff.py`, invoke:

   ```bash
   /opt/lians-worm-venv/bin/python /opt/lians-backup/upload_worm_handoff.py \
     "/backup/handoffs/${backup_id}.json" \
     --bundle "/backup/bundles/${backup_id}" \
     --output "/backup/handoffs/${backup_id}.provider-attestation.json"
   ```

   Then run the standalone verifier against the core attestation:

   ```bash
   /opt/lians-worm-venv/bin/python /opt/lians-backup/verify_worm_attestation.py \
     "/backup/handoffs/${backup_id}.provider-attestation.json"
   ```

5. Keep the existing `/backup` PVC mounted read-write for bundle, handoff, and
   attestation publication. Ensure the handoff directory is mode `0700` and the
   pod runs as UID/GID `10001`.
6. Extend egress policy only to the selected provider data/control-plane and
   identity endpoints (AWS S3/STS, GCS Storage/OAuth, or Azure Blob/ARM/Entra),
   preferably through private endpoints. Do not open unrestricted egress.
7. Treat CronJob success as valid only when the core attestation, its `.sha256`,
   its derived `.anchor.json`, and the anchor's `.sha256` all exist and remain
   non-empty after the standalone verifier rechecks both local pairs and the
   exact provider object. Export upload/verification latency and object counts,
   and alert on any pending handoff past the recovery SLO.

Do not set the deployment's WORM-complete signal from the pending handoff. Set it
only after all four attestation artifacts have been independently retained and
the standalone provider-anchor verification has succeeded.
