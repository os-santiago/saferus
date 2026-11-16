# Security boundaries

Saferus is a local demonstration backed by OpenBao, not a newly designed vault
cryptosystem. OpenBao provides the storage encryption barrier and authenticated
transit encryption. See its [security model](https://openbao.org/docs/internals/security/)
and [declarative audit configuration](https://openbao.org/docs/configuration/audit/).

## Implemented controls

- TLS 1.3 only; the clients validate the configured CA and reject redirects and
  ambient proxy settings when sending credentials to the vault.
- OpenBao and the console run non-root with read-only root filesystems, dropped
  capabilities and RuntimeDefault seccomp. Writable data/audit paths use PVCs.
- Kubernetes authentication binds the `saferus-client` service account,
  `saferus-demo` namespace and `saferus` audience. The issued reader token lasts
  60 seconds, with an explicit maximum lifetime of 300 seconds.
- The console can only perform a fixed synthetic encrypt/decrypt operation. It
  revokes its token before returning success. On failure it attempts bounded
  cleanup; expiry remains the fallback if the process or network fails.
- KV v2 requires compare-and-set writes. The bootstrap creates one synthetic
  secret and a non-exportable AES-256-GCM key. The reader cannot write KV values,
  export keys, administer the vault or read another secret path.
- Declarative audit is enabled before application configuration proceeds; tested
  token/secret strings must not appear in raw audit output. Audit metadata and
  non-string fields are not universally confidential; protect audit storage.
- Root credentials are used only for bootstrap, then revoked and removed from
  the local custody record. No operator token is issued by default.
- Client download URLs/redirects are HTTPS and host allowlisted; artifacts and
  extracted binaries are checked against reviewed SHA-256 pins. Pins provide
  integrity relative to the lock; this is not a signature/provenance assessment.

## Threat model and current limits

Trust includes the host, Kubernetes control plane, cluster administrator, pinned
upstream components and the operator's private state directory. A host/cluster
administrator can inspect workload memory, credentials or storage. NetworkPolicy
labels are defense in depth, not identity; vault policy enforces authorization.

The default is one Raft member on kind. It has no high-availability guarantee,
external KMS/HSM seal integration, distributed unseal custody, tested backup
restoration, external audit retention, or production certificate rotation.
Unseal shares co-located on the local host are deliberately **demo-only**. TLS
private keys are Kubernetes Secrets; unseal shares are kept outside Kubernetes.
Secure host memory/swap/core-dump handling remains an operator responsibility.

The synthetic console is not an authenticated administrative interface. Its
Service has no public ingress and its incoming traffic is denied by NetworkPolicy;
access uses an authorized loopback port-forward. Do not publish it externally or
extend it to accept real secret material without a separate authentication and
authorization design and review.

Changes to security boundaries need explicit human authorization and independent
review. UI automation cannot modify crypto, policies, dependencies or deployment.

## Reporting

Use GitHub private vulnerability reporting when available. Do not include real
credentials, unseal shares, customer data or raw audit logs in public issues.
Provide a minimal synthetic reproduction and the affected commit/version.
