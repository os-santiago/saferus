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
- Desktop and mobile Chromium browser walkthroughs complete the verification and
  show no horizontal overflow. The browser trusts only the known certificate SPKI.
- Go 1.27.4 build, vet and race tests pass in the pinned builder image; CI also
  runs race tests independently.
- Frontend tests and Helm rendering/security checks pass. Installer tests cover
  interrupted mount/seed creation, lost root-revocation responses and interrupted
  cluster/local-state deletion without replacing authority or overwriting data.

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
