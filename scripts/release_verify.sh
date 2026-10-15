#!/usr/bin/env bash
# Downloads the DRAFT release back from the public releases repo and proves what a user (and the
# portal) would receive: every signature verifies against the key embedded in the app, every
# checksum and the manifest agree with the bytes, the manifest names this version, and the image
# digest exists in ghcr. Anonymous pull access to the image is checked last and only WARNS: making
# the ghcr package public is a one-time owner step (RELEASING.md), not a property of this commit.
#
# Usage: GH_TOKEN=<token with contents:write on the releases repo> scripts/release_verify.sh <version>
set -euo pipefail
version="$1"
repo="open-net-worth-ai/open-net-worth-releases"
image="ghcr.io/open-net-worth-ai/open-net-worth"
here="$(cd "$(dirname "$0")" && pwd)"
key_file="$here/../desktop/src/main/update-key.ts"
embedded="$(sed -n "s/^export const UPDATE_PUBLIC_KEY = '\(RW[A-Za-z0-9+\/=]*\)';\$/\1/p" "$key_file")"
[ -n "$embedded" ] || { echo "::error::no minisign public key is embedded in the app"; exit 1; }
command -v minisign >/dev/null || { echo "::error::minisign is not installed on this runner"; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
assets="$(gh api "repos/$repo/releases?per_page=100" --jq ".[] | select(.tag_name == \"v$version\" and .draft == true) | .assets[] | \"\(.id) \(.name)\"")"
[ -n "$assets" ] || { echo "::error::no draft v$version in $repo"; exit 1; }
while read -r id name; do
  gh api -H "Accept: application/octet-stream" "repos/$repo/releases/assets/$id" > "$work/$name"
done <<< "$assets"
echo "downloaded $(echo "$assets" | wc -l | tr -d ' ') assets"

for f in SHA256SUMS manifest.json; do
  minisign -V -P "$embedded" -m "$work/$f" -x "$work/$f.minisig" >/dev/null \
    || { echo "::error::$f signature does not verify"; exit 1; }
done
(cd "$work" && sha256sum -c SHA256SUMS >/dev/null) || { echo "::error::SHA256SUMS does not match the downloaded assets"; exit 1; }
python3 "$here/release_manifest.py" check "$work"
manifest_version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$work/manifest.json")"
[ "$manifest_version" = "$version" ] || { echo "::error::manifest is for $manifest_version, not $version"; exit 1; }
echo "signatures, checksums and manifest verified"

digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"]["digest"])' "$work/manifest.json")"
docker buildx imagetools inspect "$image@$digest" >/dev/null \
  || { echo "::error::image digest $digest is not in $image"; exit 1; }
tagged="$(docker buildx imagetools inspect "$image:$version" --format '{{.Manifest.Digest}}')"
[ "$tagged" = "$digest" ] || { echo "::error::$image:$version is $tagged but the manifest says $digest"; exit 1; }
echo "image $image@$digest exists and carries tag $version"

token="$(curl -fsS "https://ghcr.io/token?service=ghcr.io&scope=repository:open-net-worth-ai/open-net-worth:pull" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' || true)"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" \
  -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json" \
  "https://ghcr.io/v2/open-net-worth-ai/open-net-worth/manifests/$digest" || true)"
if [ "$code" = "200" ]; then
  echo "anonymous pull of the image is granted"
else
  echo "::warning::anonymous pull of $image@$digest answered HTTP $code - hosted agents pull anonymously. Make the ghcr package public once (RELEASING.md) before promoting."
fi
