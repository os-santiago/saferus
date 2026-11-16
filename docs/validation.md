# Validation evidence

## Initial implementation — 2026-11-16

Observed locally before the first PR:

- Real OpenBao 2.7.2 and a single-container Go console on an isolated kind cluster.
- Kubernetes workload login, permitted KV read, forbidden cross-path read/write,
  wrong-audience/SA rejection, forbidden key export, authenticated encryption
  roundtrip, tampered-ciphertext rejection and token revocation.
- TLS 1.2 and untrusted certificate rejection; TLS 1.3 with the local CA succeeds.
- Real permitted → denied → permitted client Pods exercise NetworkPolicy.
- Real console HTTPS request authenticates using its projected workload token.
- Desktop and mobile Firefox walkthroughs complete the verification and show no
  horizontal overflow. A disposable NSS profile trusts the public demo CA with
  normal X.509 validation. Synthetic browser fixtures accept valid TLS and reject
  wrong SAN/untrusted CA certificates without modifying host trust.
- Go 1.27.4 build, vet and race tests pass in the pinned builder image; CI also
  runs race tests independently.
- Frontend tests and Helm rendering/security checks pass. Installer tests cover
  interrupted mount/seed creation, lost root-revocation responses and interrupted
  cluster/local-state deletion without replacing authority or overwriting data.
- Creation failure tests cover a lost kind response, receipt persistence failure,
  wrong nonce/provider and unidentified partial containers. A real failed create
  was cleared after confirming no provider resources remained. A trickling HTTP
  fixture proves that the parent deadline interrupts a blocked download read.

Failure-derived corrections include strict CA key-usage extensions, declarative
OpenBao audit configuration, waiting for Raft leadership/audit readiness, explicit
Helm kubeconfig selection and registration of the exact imported image digest.

The local report is retained as `verification.json` in the private demo state
directory. It contains sanitized outcomes and image identity, not credentials or
audit bodies. The browser test produces screenshots and `browser.json` under the
explicit output directory. These are observations, not signed attestations.

**Not proven:** autonomous issue-to-production AI-SDLC delivery, production HA,
backup restoration, external key custody, continuous isolation, an SLSA level or
independent penetration testing. Subsequent entries must distinguish new evidence
from these remaining gaps. Docker support uses the same installer path but has
not yet been validated on this workstation; the observed provider is Podman.

## AI-SDLC test environment bootstrap — 2026-11-16

The new namespace was installed cold in the existing, installer-owned
`ai-sdlc-saferus` kind cluster using Podman. The vault and console became ready,
and all API, TLS, authentication, authorization, audit-redaction and network
positive/negative/positive controls passed. Reapplying the same setup passed
without replacing custody. Desktop and mobile Firefox walkthroughs passed with
normal X.509 verification in isolated trust profiles.

The 32 Python/Helm tests pass, including namespace lost-create-response recovery,
foreign namespace/cluster/UID rejection, private kubeconfig handling and refusing
to overwrite a promoted console image. Helm lint passes. The original standalone
cluster remains separate; this command cannot delete the AI-SDLC cluster.

This establishes baseline test infrastructure only. No autonomous issue, merge,
promotion, release or deployment was produced by this bootstrap. The full E2E
remains unverified until AI-SDLC performs those transitions itself.

Auditor correction: once the test console exists, reapplication performs no
Helm reconciliation or image build/import. A simulated promotion during initial
build/import is rejected before Helm. The real read-only reapplication passed
again after this correction; these checks prevent baseline setup from undoing
an autonomous promotion.

The cold console path now renders the Helm template and uses create-only
Kubernetes API requests, creating NetworkPolicy before Service and Deployment.
It never uses Helm upgrade for the test console. A real Kubernetes race fixture
inserted a concurrent Deployment after the absent GET: the create failed with
AlreadyExists and preserved its UID/image. The positive create/reapply control
then passed with stable owned UIDs. The fixture scheduled no Pods and removed
its namespace with a UID precondition. Run `python -m
tools.verify_console_create_race --output /tmp/saferus-create-race.json` to repeat.

The same create-only protocol also covers the console TLS Secret. A second real
race inserts a concurrent Secret after GET and before create: its UID and bytes
remain unchanged. No apply fallback is permitted for the test console Secret.
The combined positive and conflict fixtures passed after this correction.

Environment verification now selects the active immutable Deployment image,
requires a fully observed single ready replica, and compares UID/generation/image
before and after probes and API checks. Probes and the report use that same
image, even when the saved baseline differs. The real active-image verification
passed; a concurrent change fails verification instead of attributing evidence
to the wrong image.

## Bounded console pressure and recovery — 2026-11-16

`python3 -m tools.verify_console_pressure` now validates an existing owned demo
without changing application code, custody, images or AI-SDLC workflow state.
The test sends 20 fixed synthetic requests: two bursts of eight concurrent
requests, each bracketed by successful sequential verification. X.509 validation
uses only the demo CA, TLS 1.3 is required, ambient proxies are excluded, response
size is bounded and unexpected result fields/statuses fail the check. It records
only counters, durations and public workload identity.

The real AI-SDLC test-production installation passed: 6 requests completed with
verification and token revocation, 14 received the exact HTTP 429 saturation
response, and both recovery checks passed. Successful durations were 24.11 ms
minimum, 25.93 ms median and 32.13 ms maximum, including loopback port forwarding.
Deployment UID `99d5fda6-eb41-482a-bce1-cc5dc0ab3f4c`, generation 1, and image
`localhost/saferus-console@sha256:15cf1ab23f143c3e46ad86dded0d8e459e91fa05817fef4012b3c0b8f0227933`
remained stable before and after the check. A previous run also passed.

All 35 Python/Helm tests and Helm lint passed. This is a bounded concurrency and
recovery observation, not a throughput benchmark, percentile estimate, enterprise
SLO, load-balancer test, or autonomous AI-SDLC E2E proof. The default application
still intentionally admits one synthetic verification at a time.

Auditor correction: the pressure client rejects redirects with the existing
`NoRedirect` policy. A real local HTTP server returned 301/302/303/307/308 to a
separate path; none of those destinations were contacted. A request timeout
regression verifies no automatic retry. The corrected tool passed another real
20-request run with stable workload identity and normal X.509 checks.

## Explicit Konflux test environment — 2026-11-16

The test-production baseline now supports the explicitly selected, installer-owned
Konflux cluster used by AI-SDLC's build infrastructure. Selection validates the
cluster name before constructing paths and derives matching runtime/application
state roots. The legacy `ai-sdlc-saferus` defaults remain available. Before an
application namespace intent, setup checks the Konflux owner, Saferus runtime
scope and the original runtime namespace UID/creation nonce against the live API.
It cannot delete the shared cluster or adopt a namespace by name.

Cold installation and reapplication on Kubernetes v1.36.4 passed with the same
application namespace UID and console identity. Real API, TLS, authentication,
authorization, encryption/tampering, revocation, audit-redaction and
positive/negative/positive NetworkPolicy checks passed. Firefox desktop/mobile
checks passed with normal X.509 validation and separate CA trust. The bounded
pressure check passed with six successful revocations, fourteen HTTP 429
rejections and both recovery checks. The 41 Python/Helm tests and Helm lint pass.
See the [sanitized evidence](evidence/2026-11-16-konflux-environment.json).

Two findings from the preventive audit were corrected before the cold install:
the runtime state default now follows the selected cluster, and validation occurs
before custody path derivation in all three entrypoints. Explicit alternative
paths still require matching live identities. This establishes the baseline
application beside the builder; it is not an autonomous promotion, release or
issue-to-production run. Synthetic co-located custody and single-node limitations
remain; full platform readiness belongs to the separate AI-SDLC preflight.
