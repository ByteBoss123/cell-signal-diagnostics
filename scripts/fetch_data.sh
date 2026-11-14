#!/usr/bin/env bash
# Fetch the raw NASA PCoE and CALCE CS2 files from a public GitHub mirror at a pinned commit,
# then verify every file against data/raw_manifest.sha256. Nothing is redistributed in this repo.
set -euo pipefail
MIRROR=https://github.com/XiuzeZhou/RUL
COMMIT=26f12968ffece1e14873273841889982af8135d5
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git -C "$TMP" init -q
git -C "$TMP" remote add origin "$MIRROR"
git -C "$TMP" sparse-checkout set datasets/NASA datasets/CALCE
git -C "$TMP" fetch -q --depth 1 origin "$COMMIT"
git -C "$TMP" checkout -q FETCH_HEAD
mkdir -p "$ROOT/data/raw/nasa" "$ROOT/data/raw/calce"
cp "$TMP"/datasets/NASA/B00*.mat "$ROOT/data/raw/nasa/"
cp -r "$TMP"/datasets/CALCE/CS2_3* "$ROOT/data/raw/calce/"
echo "$COMMIT" > "$ROOT/data/raw/SOURCE_COMMIT.txt"
cd "$ROOT/data/raw" && sha256sum -c --quiet ../raw_manifest.sha256 && echo "raw files verified"
