#!/usr/bin/env bash
set -euo pipefail
# Public Google emulator, checked before execution. Requires Java 17+ and curl.
jar="${TMPDIR:-/tmp}/flashcart-firestore-emulator-v1.19.8.jar"
if [[ ! -f "$jar" ]]; then
  curl --fail --location --silent --show-error \
    https://storage.googleapis.com/firebase-preview-drop/emulator/cloud-firestore-emulator-v1.19.8.jar \
    --output "$jar"
fi
expected=9d43599ed6151199e8d604dc87fac51218e49e5f3a48519b1ae560bbe5e3382d
actual=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$jar")
if [[ "$actual" != "$expected" ]]; then
  echo "Firestore emulator checksum mismatch" >&2
  exit 1
fi
exec java -jar "$jar" --host 127.0.0.1 --port 18080
