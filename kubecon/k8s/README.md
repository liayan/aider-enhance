# Kubernetes scaffold (draft)

Same workload and probes as the local backends, run as
[agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) `Sandbox`
objects on three isolation tiers.

Status: **manifests validated, not yet run on a cluster.** Every file passes
`kubeconform -strict` against the Kubernetes schemas, and the `Sandbox` objects
validate against the agent-sandbox `v1beta1` CRD (upstream commit `68db683`,
checked 26 Sep 2026; re-checked with unknown fields rejected against v1.0.5,
`43ef54a`, on 3 Oct 2026). The first cluster run is the next task on the
[roadmap](../../README.md#roadmap).

| Tier | `runtimeClassName` | Kernel |
|---|---|---|
| `runc` | (none) | node kernel |
| `gvisor` | `gvisor` (handler `runsc`) | gVisor Sentry, on the node kernel |
| `kata` | `kata-clh` (from kata-deploy; minimal VMM). `kata-fc` is a stretch goal; `kata-qemu` only for comparison | guest kernel |

## Files

| File | What |
|---|---|
| `Containerfile` | image with `src/` and `workload/` baked in (no host mounts in a cluster) |
| `00-namespaces.yaml` | `agent-lab` (Pod Security `restricted`), `agent-gateway`, `agent-evidence` |
| `05-runtimeclass-gvisor.yaml` | gVisor RuntimeClass; kata-deploy creates the Kata ones |
| `10-gateway.yaml` | model gateway; the API key is a Secret in `agent-gateway` only |
| `20-evidence-sink.yaml` | logs any request that gets through; read from outside by the evaluator |
| `30-networkpolicy.yaml` | one policy for all tiers: deny all, allow DNS + gateway |
| `40/41/42-sandbox-*.yaml` | one `Sandbox` per tier, identical except `runtimeClassName` |

Every agent pod: a hard `activeDeadlineSeconds` limit, non-root, no service-account token, no service links, seccomp
`RuntimeDefault`, all capabilities dropped, read-only root filesystem, memory
and CPU limits.

## Cluster prerequisites

- A node with KVM (for Kata). The Ubuntu 24.04 host already used for
  Firecracker works.
- containerd, plus a CNI that **enforces** NetworkPolicy (Calico or Cilium).
  kind's default (kindnet) also enforces it; Flannel does not.
- gVisor (`runsc`) registered with containerd as handler `runsc`.
- Kata via kata-deploy (`kata-clh`). `kata-fc` additionally needs the
  devmapper snapshotter.
- agent-sandbox core:
  `kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/<version>/sandbox.yaml`
  (pin the version you tested).

## Apply order

```bash
podman build -t agent-lab:1 -f kubecon/k8s/Containerfile .   # + load/push to the node
kubectl apply -f kubecon/k8s/00-namespaces.yaml -f kubecon/k8s/05-runtimeclass-gvisor.yaml
kubectl -n agent-gateway create secret generic model-key --from-literal=api-key="$MODEL_API_KEY"
kubectl apply -f kubecon/k8s/10-gateway.yaml -f kubecon/k8s/20-evidence-sink.yaml -f kubecon/k8s/30-networkpolicy.yaml
# per run: create ConfigMap agent-run-input (see "Still to build"), then a tier:
kubectl apply -f kubecon/k8s/41-sandbox-gvisor.yaml
kubectl -n agent-lab logs -f -l tier=gvisor     # wait for "DONE rc=..."
```

## Still to build

1. **Per-run input generator**: reuse `prepare_run` from `src/common/lib.sh`
   to produce `run.env`, task files, acceptance tests and the resolved
   injection fixture, then `kubectl create configmap agent-run-input`. Point
   `EXFIL_URLS` at `http://egress-sink.agent-evidence.svc:9100`.
2. **Collector + evaluator**: `kubectl cp` the declared outputs out of the pod,
   read the sink's logs as host-side evidence, then run the existing
   `collect.py` / `evaluate.py` / `footprint.py`.
3. **Cluster checks** in `probe_runner.py`: service-account token, API server
   reachability, metadata address, cross-namespace Services.
4. A `run-tier.sh <runc|gvisor|kata>` wrapper that does all of the above and
   writes `results/` in the same layout as the local backends, so
   `compare-results.sh` works unchanged.
