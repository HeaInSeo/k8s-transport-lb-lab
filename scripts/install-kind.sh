#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/versions.env"
case "$(uname -s)" in Linux) os=linux;; Darwin) os=darwin;; *) echo 'Linux/macOS only' >&2; exit 1;; esac
case "$(uname -m)" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) echo 'Unsupported architecture' >&2; exit 1;; esac
mkdir -p "$ROOT/.lab/bin"
tmp=$(mktemp -d "$ROOT/.lab/kind-download.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
asset="kind-$os-$arch"
base="https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION"
curl -fsSL --retry 3 "$base/$asset" -o "$tmp/$asset"
curl -fsSL --retry 3 "$base/$asset.sha256sum" -o "$tmp/checksum"
python3 - "$tmp/$asset" "$tmp/checksum" <<'PY'
import hashlib, pathlib, sys
binary, sums = map(pathlib.Path, sys.argv[1:])
expected = sums.read_text().split()[0]
actual = hashlib.sha256(binary.read_bytes()).hexdigest()
if len(expected) != 64 or actual != expected:
    raise SystemExit('kind checksum mismatch; refusing installation')
print('Verified kind SHA256:', actual)
PY
chmod 0755 "$tmp/$asset"
mv "$tmp/$asset" "$ROOT/.lab/bin/kind"
"$ROOT/.lab/bin/kind" version
