# Third-party components

Saferus's original wrapper, console and automation code use Apache-2.0. Upstream
components retain their own licenses and notices:

- [OpenBao](https://github.com/openbao/openbao), version 2.7.2, MPL-2.0.
- [OpenBao Helm chart](https://github.com/openbao/openbao-helm), version 0.30.7,
  MPL-2.0. The unmodified chart archive is vendored under `charts/saferus/charts/`.
- Go, Node.js, Kubernetes kind/kubectl and Helm are build/test or installation
  dependencies; their respective upstream licenses apply.

Image, chart and tool pins identify the reviewed input bytes. They do not imply
an upstream signature verification or a vulnerability-free component.
