# Saferus

A Kubernetes secrets vault that applications consume directly over HTTPS, without
an agent, injector or CSI sidecar. Saferus packages **OpenBao**, rather than
implementing a new cryptographic engine, and includes a small console that proves
workload authentication, authenticated encryption and token revocation.

**Current scope: an isolated, reproducible local demo.** The repository does not
claim production readiness, a SLSA level, FIPS certification or an independent
security assessment. See [security boundaries](SECURITY.md).

## Run the demo

Requirements: Linux amd64, Python 3.11+, OpenSSL, a working Podman or Docker
installation, and sufficient capacity for a kind cluster. The first run downloads
digest-pinned kind, kubectl and Helm clients, plus container images. Go and Node
are not required on the host to run the demo.

```bash
gh repo clone os-santiago/saferus
cd saferus
./saferus demo --provider podman
```

The command creates the owned `saferus-demo` kind cluster and namespace, issues a
local TLS certificate, installs the sealed vault, initializes **synthetic demo
custody**, configures the least-privilege workload role, revokes the bootstrap
root token, builds the console and runs real API/network checks. It creates no
public ingress and never prints credentials or secret values.

The local custody directory is `~/.local/state/saferus/demo` (0700). It contains
private TLS material and **three co-located unseal shares with threshold two**.
This arrangement enables reproducible tests; it is not distributed production
key custody. Do not put real secrets in this demo. Data and audit volumes persist
until the owned cluster is explicitly destroyed.

Reapply the same command to reconcile the demo, or validate the current install:

```bash
./saferus verify --provider podman
./saferus tools
```

`tools` prints verified client paths. Use the printed kubectl path to access the
console on loopback:

```bash
~/.cache/saferus/tools/kubectl-1.35.8 \
  --kubeconfig ~/.local/state/saferus/demo/kubeconfig \
  -n saferus-demo port-forward service/saferus-console 8443:8443 --address 127.0.0.1
```

Open `https://localhost:8443` using a separate browser profile that trusts the demo
CA at `~/.local/state/saferus/demo/ca.crt`. Keep its private key private. Do not
disable TLS verification globally. Certificates last two days; this prototype
does not yet automate certificate renewal.

The console accepts no secret or plaintext input. **Run verification** uses a
projected Kubernetes identity, encrypts and decrypts a fixed synthetic message,
then revokes the short-lived token. It displays only the verification result.

## What is installed

| Component | Purpose | Boundary |
|---|---|---|
| OpenBao 2.7.2 | KV v2 storage and AES-256-GCM transit operations | TLS 1.3, Raft, declarative audit, sealed by default |
| Saferus console | Demonstrate direct workload access | One non-root scratch container; no arbitrary secret input |
| Kubernetes identity | Short-lived, audience-bound authentication | Fixed service account and namespace; scoped vault policy |
| Helm 0.30.7 upstream chart | Reproducible OpenBao resources | Vendored archive and image digest pins |

Both application Pods have one container. The vault can create TokenReviews;
the console cannot access the Kubernetes API. NetworkPolicy permits only the
reviewed paths. The default chart leaves the console disabled until its reviewed
image digest is supplied. The upstream administrative web UI is disabled.

## Validation and development

```bash
go build ./...
go vet ./...
go test -race ./...  # requires a C compiler
node --test tests/*.test.mjs
python3 -m pip install -r requirements-dev.txt
helm lint charts/saferus
python3 -m unittest discover -s tests -v
```

The real demo checks unauthorized access, wrong audience/service account,
cross-path access, forbidden writes/key export, ciphertext tampering, revocation,
TLS downgrade/untrusted certificate rejection and redaction of tested secret
strings in audit output. A permitted → denied → permitted Pod sequence checks
actual network enforcement. Go tests cover the console's API boundary and token
cleanup; installer tests cover interrupted setup and cleanup.

Optional desktop/mobile browser verification:

```bash
python3 -m pip install -r requirements-browser.txt
python3 -m playwright install chromium
python3 -m tools.verify_browser --output /tmp/saferus-browser
```

The browser test trusts only the known console certificate's public-key pin;
API tests separately validate its issuing CA. See [validation](docs/validation.md)
for observed results and limitations.

## Remove the demo

```bash
./saferus destroy --provider podman --confirm-local-demo
```

This verifies cluster ownership, deletes the owned kind cluster and removes its
known local custody files. It retains the sanitized verification report and does
not remove unrelated files, container image caches or external backups. File
removal is not a secure-erasure guarantee. An interrupted deletion can resume
from its durable, UID-bound intent; a replacement cluster is never adopted.

## AI-SDLC reference application

Saferus is the intended first autonomous E2E target for
[AI-SDLC](https://github.com/os-santiago/ai-sdlc). The initial scenario will improve
token-lifetime presentation through an issue, independent tests/review, PR,
merge, artifact construction and deployment to a separate test-production
namespace. **That integration is not yet operational.**

Low-risk automation will be restricted to the reviewed presentation recipe.
Cryptography, authentication, policies, key custody, deployment and dependency
changes require explicit human authorization. See [contribution rules](CONTRIBUTING.md)
and [architecture](docs/architecture.md).
