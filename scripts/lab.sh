#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
source versions.env
export PATH="$ROOT/.lab/bin:$PATH" KIND_EXPERIMENTAL_PROVIDER=docker
NAME=${CLUSTER_NAME:-k8s-transport-lb-lab}
[[ "$NAME" =~ ^k8s-transport-lb-lab(-[a-z0-9-]+)?$ && ${#NAME} -le 50 ]] || { echo 'Unsafe cluster name' >&2; exit 1; }
REQUESTS=${REQUESTS:-600}
[[ "$REQUESTS" =~ ^[0-9]+$ ]] && (( REQUESTS >= 90 && REQUESTS <= 100000 )) || { echo 'REQUESTS must be 90..100000' >&2; exit 1; }
STATE="$ROOT/.lab/$NAME"
export KUBECONFIG="$STATE/kubeconfig"
k() { kubectl --kubeconfig "$KUBECONFIG" --context "kind-$NAME" --request-timeout=20s "$@"; }
die() { echo "$*" >&2; exit 1; }
exists() { local clusters; clusters=$(kind get clusters); grep -Fxq "$NAME" <<<"$clusters"; }
owned() {
  [[ -s "$STATE/owner" && -s "$KUBECONFIG" ]] || die 'No ownership marker: refusing to touch this cluster.'
  local remote; remote=$(k -n kube-system get configmap transport-lab-owner -o jsonpath='{.data.token}')
  [[ "$remote" == "$(cat "$STATE/owner")" ]] || die 'Ownership mismatch: refusing to touch this cluster.'
}
doctor() {
  for tool in docker kind kubectl python3; do command -v "$tool" >/dev/null || die "Missing $tool (kind: make install-kind; or use GitHub Actions)."; done
  docker info >/dev/null 2>&1 || die 'Docker daemon is not accessible. Podman is not a tested v0.1 provider.'
  [[ "$(kind version)" == "kind $KIND_VERSION "* ]] || die "Expected kind $KIND_VERSION; run make install-kind."
  python3 -c 'import shutil; n=shutil.disk_usage(".").free; print("Free disk: %.1f GiB" % (n/2**30)); assert n>=8*2**30, "Need >=8 GiB free disk; use GitHub Actions instead"'
  echo 'Prerequisites OK; dedicated kubeconfig will be used.'
}
image_name() {
  python3 - <<'PY'
import hashlib, pathlib
paths = [pathlib.Path(p) for p in ('go.mod','go.sum','Dockerfile')]
paths += list(pathlib.Path('cmd').rglob('*.go')) + list(pathlib.Path('internal').rglob('*.go'))
h = hashlib.sha256()
for p in sorted(paths):
    h.update(str(p).encode()); h.update(b'\0'); h.update(p.read_bytes())
print('k8s-transport-lb-lab:'+h.hexdigest()[:16])
PY
}
up() {
  doctor
  if exists; then owned; else
    mkdir -p "$STATE"
    python3 -c 'import uuid; print(uuid.uuid4())' > "$STATE/owner"
    kind create cluster --name "$NAME" --image "$NODE_IMAGE" --config deploy/kind.yaml --kubeconfig "$KUBECONFIG" --wait 180s
    k -n kube-system create configmap transport-lab-owner --from-literal="token=$(cat "$STATE/owner")"
  fi
  IMAGE=$(image_name)
  docker build --build-arg "GO_VERSION=$GO_VERSION" -t "$IMAGE" .
  kind load docker-image "$IMAGE" --name "$NAME"
  sed "s|LAB_IMAGE|$IMAGE|g" deploy/workload.yaml > "$STATE/workload.yaml"
  k apply -f "$STATE/workload.yaml"
  k -n kube-system rollout status daemonset/kindnet --timeout=120s
  k -n kube-system rollout status daemonset/kube-proxy --timeout=120s
  k -n lb-lab rollout status deployment/echo --timeout=120s
  printf '%s\n' "$IMAGE" > "$STATE/image"
}
smoke() {
  owned
  [[ -s "$STATE/image" ]] || die 'Run make up first.'
  IMAGE=$(cat "$STATE/image")
  RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  OUT="$ROOT/reports/$RUN_ID"; mkdir -p "$OUT"
  JOB="probe-$(date +%s)-$$"
  { date -u; kind version; echo "cluster=$NAME"; echo "node_image=$NODE_IMAGE"; echo "app_image=$IMAGE"; git rev-parse HEAD; git status --short; docker image inspect "$IMAGE" --format '{{.Id}}'; } > "$OUT/environment.txt"
  k version -o json > "$OUT/kubernetes-version.json"
  k -n lb-lab get pods -l app=transport-echo -o json > "$OUT/pods-before.json"
  k -n lb-lab get endpointslices -o json > "$OUT/endpoints-before.json"
  k -n kube-system get daemonset kindnet -o json > "$OUT/kindnet.json"
  k -n kube-system get configmap kube-proxy -o json > "$OUT/kube-proxy.json"
  sed -e "s|LAB_IMAGE|$IMAGE|g" -e "s|LAB_JOB|$JOB|g" -e "s|LAB_REQUESTS|$REQUESTS|g" deploy/job.yaml > "$OUT/job.yaml"
  k apply -f "$OUT/job.yaml"
  status=''
  for ((i=0; i<100; i++)); do
    status=$(k -n lb-lab get job "$JOB" -o jsonpath='{.status.succeeded},{.status.failed}')
    [[ "$status" == '1,'* || "$status" == *',1' ]] && break
    sleep 2
  done
  k -n lb-lab logs "job/$JOB" --pod-running-timeout=10s > "$OUT/job.log" 2>&1 || true
  k -n lb-lab get pods -o wide > "$OUT/pods.txt"
  k -n lb-lab get pods -l app=transport-echo -o json > "$OUT/pods-after.json"
  k -n lb-lab get events --sort-by=.metadata.creationTimestamp > "$OUT/events.txt"
  k -n lb-lab get endpointslices -o json > "$OUT/endpoints-after.json"
  python3 scripts/report.py "$OUT"
  ln -sfn "$RUN_ID" "$ROOT/reports/latest"
  echo "Report: $OUT/index.html"
  [[ "$status" == '1,'* ]] || die "Probe Job failed or timed out: $status; see $OUT"
  python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["passed"] else 1)' "$OUT/results.json"
}
clean() {
  if exists; then owned; kind delete cluster --name "$NAME" --kubeconfig "$KUBECONFIG"; fi
  echo 'Only this owned lab cluster was removed. Reports and build cache remain.'
}
ACTION=${1:-help}
case "$ACTION" in doctor) doctor; exit;; demo|up|smoke|clean) ;; *) die 'Use make demo | up | smoke | doctor | clean';; esac
mkdir -p "$ROOT/.lab"
LOCK="$ROOT/.lab/$NAME.lock"
mkdir "$LOCK" 2>/dev/null || die 'Another lab operation holds the lock. Do not remove it while a run is active.'
finish() {
  rc=$?
  if ((rc != 0)); then
    fail="$ROOT/reports/failure-$(date -u +%Y%m%dT%H%M%SZ)-$$"; mkdir -p "$fail"
    echo "Action=$ACTION exit=$rc cluster=$NAME" > "$fail/status.txt"
    if [[ -s "$KUBECONFIG" ]]; then k get pods -A -o wide > "$fail/pods.txt" 2>&1 || true; fi
    echo "Failure diagnostics: $fail" >&2
  fi
  rmdir "$LOCK" || true
  exit "$rc"
}
trap finish EXIT
case "$ACTION" in demo) up; smoke;; up) up;; smoke) smoke;; clean) clean;; esac
