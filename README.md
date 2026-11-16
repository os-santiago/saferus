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

Creation records a private intent before invoking kind. After an interrupted
creation, the next run recovers ownership only when the Kubernetes node carries
the original nonce and the authenticated API supplies the cluster UID. If no
cluster or partial provider container exists, the recorded attempt can restart
or be cleared with `destroy`. Unidentified partial resources are retained for
inspection; Saferus never adopts or deletes them solely by name.

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

## AI-SDLC test production

To provision the baseline vault in the dedicated AI-SDLC kind installation:

```bash
./saferus test-environment --provider podman
./saferus verify-environment --provider podman
```

This uses the existing installer-owned `ai-sdlc-saferus` cluster and creates the
`ai-sdlc-production` namespace. AI-SDLC must already have a valid installation
record at `~/.local/state/ai-sdlc/ai-sdlc-saferus`; use `--ai-sdlc-state PATH` for
an alternate location. Application custody is separate, under
`~/.local/state/saferus/ai-sdlc-production`, or an explicit `--state PATH`.

Setup verifies the live cluster UID, records its namespace creation intent and
rejects foreign namespaces or changed UIDs. It cannot destroy the AI-SDLC
cluster. Once the console exists, reapplication verifies it without running Helm or
rebuilding its image; it refuses a baseline image changed by a later promotion.
Initial console creation uses Helm-rendered manifests with create-only API
writes, so a concurrent Deployment causes a conflict instead of an overwrite. This is initial infrastructure setup, not an AI-SDLC workflow
stage: autonomous requests still enter only through issues.

An installer-owned Konflux cluster can host the baseline beside the build
infrastructure. Select it explicitly after AI-SDLC has installed its frozen
Saferus runtime there:

```bash
./saferus test-environment --provider podman \
  --ai-sdlc-cluster ai-sdlc-konflux-reviewed
./saferus verify-environment --provider podman \
  --ai-sdlc-cluster ai-sdlc-konflux-reviewed
```

The selector derives the matching AI-SDLC state at
`~/.local/state/ai-sdlc/ai-sdlc-konflux-reviewed` and separate application custody
at `~/.local/state/saferus/ai-sdlc-konflux-reviewed-production`. Explicit
`--ai-sdlc-state` and `--state` paths remain supported. The cluster name is
validated before deriving paths. Setup requires the original Konflux ownership,
Saferus installation scope and runtime namespace UID/creation nonce to match the
live API before any application namespace creation. A name or directory alone
cannot authorize adoption. This remains initial synthetic infrastructure setup,
not an autonomous promotion or an assurance of production readiness.

Browser and pressure checks accept the same selector, for example
`python3 -m tools.verify_browser --ai-sdlc-cluster ai-sdlc-konflux-reviewed
--output /tmp/saferus-konflux-browser` and
`python3 -m tools.verify_console_pressure --ai-sdlc-cluster
ai-sdlc-konflux-reviewed --output /tmp/saferus-konflux-pressure.json`.

To verify the browser against this environment with normal certificate checks:

```bash
python -m tools.verify_browser \
  --ai-sdlc-state ~/.local/state/ai-sdlc/ai-sdlc-saferus \
  --output /tmp/saferus-test-browser
```

The browser procedure requires Playwright Firefox and NSS `certutil`. It trusts
only the local demo CA in a disposable profile. All synthetic custody and
single-node limitations of the standalone demo still apply.

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

Optional desktop/mobile browser verification requires NSS `certutil`
(`nss-tools` on Fedora or `libnss3-tools` on Debian/Ubuntu):

```bash
python3 -m pip install -r requirements-browser.txt
python3 -m playwright install firefox
python3 -m tools.verify_browser --output /tmp/saferus-browser
python3 -m tools.verify_browser_trust
```

The browser test imports the public demo CA into a disposable Firefox/NSS
profile and preserves normal X.509 verification. It does not alter host trust.
The separate synthetic-certificate test checks valid TLS, wrong SAN and an
untrusted CA. See [validation](docs/validation.md)
for observed results and limitations.

A bounded synthetic concurrency check exercises saturation rejection and recovery:

```bash
python3 -m tools.verify_console_pressure --output /tmp/saferus-pressure.json
# Or select the existing AI-SDLC test environment:
python3 -m tools.verify_console_pressure \
  --ai-sdlc-state ~/.local/state/ai-sdlc/ai-sdlc-saferus \
  --output /tmp/saferus-test-pressure.json
```

It sends exactly 20 fixed synthetic requests, including two bursts of eight.
Successful responses must confirm verification and token revocation; saturation
must return the bounded HTTP 429 response, and sequential requests must recover.
The report binds the stable Deployment UID, generation and immutable image.
TLS verification remains enabled. Timings include loopback port forwarding and
are observations, not a capacity benchmark, percentile estimate or enterprise SLO.

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
