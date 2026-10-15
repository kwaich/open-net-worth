#!/usr/bin/env bash
# Publishes the verified DRAFT v<version> in the public releases repo and marks it latest - from
# this moment the site's download links and every installed app's updater offer it. Run only by
# the manually approved `promote` job of release.yml.
#
# Usage: GH_TOKEN=<token with contents:write on the releases repo> scripts/release_promote.sh <version>
set -euo pipefail
version="$1"
repo="open-net-worth-ai/open-net-worth-releases"
id="$(gh api "repos/$repo/releases?per_page=100" --jq ".[] | select(.tag_name == \"v$version\" and .draft == true) | .id")"
[ -n "$id" ] || { echo "::error::no draft v$version in $repo to promote"; exit 1; }
gh api -X PATCH "repos/$repo/releases/$id" -F draft=false -f make_latest=true >/dev/null
echo "::notice::v$version is now the latest release"
