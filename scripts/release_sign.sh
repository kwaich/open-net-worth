#!/usr/bin/env bash
# Signs a release directory's SHA256SUMS and manifest.json with the owner's minisign key and
# verifies both signatures against the public key compiled into the desktop app
# (desktop/src/main/update-key.ts) - the same key the installed apps and `onw-portal
# release-agents` trust. A checksum or manifest published beside the download proves nothing on
# its own: whoever can replace an asset can replace those too, so the signature is the proof.
#
# Missing secret, no embedded key, or a signature that does not verify against that key all fail
# the release: never publish what installed apps and the portal would refuse. Owner steps to
# create the key pair: RELEASING.md.
#
# Usage: ONW_MINISIGN_SECRET_KEY=... scripts/release_sign.sh <version> <release-dir>
set -euo pipefail
version="$1"
dir="$2"
key_file="$(dirname "$0")/../desktop/src/main/update-key.ts"
embedded="$(sed -n "s/^export const UPDATE_PUBLIC_KEY = '\(RW[A-Za-z0-9+\/=]*\)';\$/\1/p" "$key_file")"
[ -n "$embedded" ] || { echo "::error::no minisign public key is embedded in desktop/src/main/update-key.ts; see RELEASING.md (minisign key)"; exit 1; }
[ -n "${ONW_MINISIGN_SECRET_KEY:-}" ] || { echo "::error::ONW_MINISIGN_SECRET_KEY is not set; the release cannot be signed. See RELEASING.md (minisign key)"; exit 1; }
command -v minisign >/dev/null || { echo "::error::minisign is not installed on this runner"; exit 1; }
for f in SHA256SUMS manifest.json; do
  [ -f "$dir/$f" ] || { echo "::error::$dir/$f does not exist; run scripts/release_manifest.py generate first"; exit 1; }
done

work="$(mktemp -d)"
secret="$work/minisign.key"
trap 'rm -rf "$work"' EXIT
(umask 077; printf '%s\n' "$ONW_MINISIGN_SECRET_KEY" > "$secret")
# The key is generated passwordless (`minisign -G -W`): CI has no tty to prompt on and the
# GitHub secret store is what protects it.
for f in SHA256SUMS manifest.json; do
  minisign -S -s "$secret" -m "$dir/$f" -x "$dir/$f.minisig" -t "Open Net Worth $version" </dev/null \
    || { echo "::error::minisign could not sign $f (a password-protected or malformed key?)"; exit 1; }
  minisign -V -P "$embedded" -m "$dir/$f" -x "$dir/$f.minisig" >/dev/null \
    || { echo "::error::the $f signature does not verify against the public key embedded in the app - ONW_MINISIGN_SECRET_KEY is not the key for update-key.ts"; exit 1; }
done
echo "signed SHA256SUMS and manifest.json for $version"
