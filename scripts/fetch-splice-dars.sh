#!/usr/bin/env bash
set -euo pipefail

SPLICE_REF="dd4de78f621e8066a810b31185ab541f273bd60f"
BASE_URL="https://raw.githubusercontent.com/hyperledger-labs/splice/${SPLICE_REF}/daml/dars"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$ROOT/examples/dars}"

mkdir -p "$DEST"
while read -r _ name; do
  [ -f "$DEST/$name" ] || curl -sSfL --retry 3 -o "$DEST/$name" "$BASE_URL/$name"
done < "$ROOT/scripts/splice-dars.sha256"

(cd "$DEST" && shasum -a 256 -c "$ROOT/scripts/splice-dars.sha256")
