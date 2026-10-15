#!/usr/bin/env bash
# Uploads a staged release directory to the PUBLIC releases repo as a DRAFT v<version>. A draft is
# invisible to the site's download links and the update feed ("latest" never points at a draft),
# so nothing reaches a user until `release_promote.sh` publishes it. Re-running for a version
# whose earlier DRAFT exists replaces it; an already published version is refused.
#
# Usage: GH_TOKEN=<token with contents:write on the releases repo> scripts/release_draft.sh <version> <release-dir> <source-sha>
set -euo pipefail
version="$1"
dir="$2"
sha="$3"
repo="open-net-worth-ai/open-net-worth-releases"
test -n "${GH_TOKEN:-}" || { echo "::error::GH_TOKEN is not set"; exit 1; }
for f in manifest.json manifest.json.minisig SHA256SUMS SHA256SUMS.minisig; do
  [ -f "$dir/$f" ] || { echo "::error::$dir/$f is missing - the release is not signed"; exit 1; }
done

existing="$(gh api "repos/$repo/releases?per_page=100" --jq ".[] | select(.tag_name == \"v$version\") | \"\(.id) \(.draft)\"")"
if [ -n "$existing" ]; then
  [ "${existing#* }" = "true" ] || { echo "::error::v$version is already published in $repo; versions are immutable - release a new one"; exit 1; }
  echo "replacing the earlier draft of v$version"
  gh api -X DELETE "repos/$repo/releases/${existing% *}" >/dev/null
fi

notes="Open Net Worth $version.

Download the desktop app for your computer from https://opennetworth.ai - installed apps update themselves. Server installs: \`install-server.sh\` (PyPI) or \`docker-compose.yml\` (pinned image). Verify any file against \`SHA256SUMS\` and its minisign signature; \`manifest.json\` is the signed record of this release (source commit, image digest, checksums).

Source: $sha"
gh release create "v$version" "$dir"/* --repo "$repo" --draft \
  --title "Open Net Worth $version Beta" --notes "$notes"
echo "draft v$version created in $repo"
