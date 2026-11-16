# Saferus delivery rules

Saferus packages OpenBao for agentless HTTPS consumption on Kubernetes. Do not
implement new cryptographic primitives or claim certification/production security
from a demo. Preserve upstream component licenses and pinned dependencies.

- Ship through focused PRs with passing tests and independent review.
- Never commit credentials, unseal shares, private keys, raw audit logs or secret values.
- Crypto, authentication, authorization, key custody, network policy and supply-chain
  changes require explicit human authorization. Automated low-risk work is limited
  to the reviewed demo/UI recipe and must not expand its file allowlist.
- AI-SDLC consumes issues only. Do not invoke its internal workflow stages manually.
- Keep TLS verification enabled, bounded requests and least-privilege roles.
- Run `python3 -m unittest discover -s tests -v` and `helm lint charts/saferus`.
  Changes to installation must pass the real disposable kind demo before merge.
- Record evidence and limitations in `docs/validation.md`; do not claim E2E from
  an isolated installation or a manually invoked build.
