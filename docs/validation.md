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

The 29 Python/Helm tests pass, including namespace lost-create-response recovery,
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
