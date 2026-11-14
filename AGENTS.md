# Repository contract

This is a learning and reproducibility lab, not a production transport framework.
Keep the default demo independent of Cilium, meshes, Envoy and external application repositories.
Do not alter go-grpc-kit or mutual-tls-in-go, and do not relocate business RPC contracts here.

Before committing: `make ci`. For Kubernetes claims, run `make demo` and retain its actual artifacts.
Unit tests with manual resolvers are NOT evidence of Kubernetes DNS or kube-proxy behavior.
Never present fixture values or expected results as measurements. Failed setup must remain failed.
Preserve control cases where a single connection intentionally stays on one backend.
Do not use port-forward as a substitute for the Service load-balancing test path.

Never touch a cluster without the repository name guard, dedicated kubeconfig, and matching ownership markers.
No global Docker prune, no deletion of unrelated files, no changes to the user's default kubeconfig.
No production credentials, kubeconfigs or private keys in Git or report artifacts.
Changes to dependency, action or node-image pins must be explicit and revalidated.
Add new scenarios as opt-in profiles; document unimplemented work honestly in docs/ROADMAP.md.
