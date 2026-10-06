#!/usr/bin/env bash
# Run as the build user; no server secret is needed.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(git -C "$script_dir" rev-parse --show-toplevel)"
expected_commit="${1:?Usage: bash build_release.sh REVIEWED_COMMIT}"
expected_commit="$(git -C "$repo_dir" rev-parse --verify "${expected_commit}^{commit}")"
actual_commit="$(git -C "$repo_dir" rev-parse HEAD)"
[[ "$actual_commit" == "$expected_commit" ]] || { echo 'FAIL: checkout differs from reviewed commit' >&2; exit 1; }
if ! git -C "$repo_dir" diff --quiet || ! git -C "$repo_dir" diff --cached --quiet; then
  echo 'FAIL: tracked source differs from reviewed commit' >&2
  exit 1
fi
if [[ -n "$(git -C "$repo_dir" ls-files --others --exclude-standard)" ]]; then
  echo 'FAIL: checkout has untracked files; use a clean build checkout' >&2
  exit 1
fi
# Vite loads .env files and VITE_* variables into the browser build.
# This deployment has no build-time configuration or embedded credentials.
if find "$repo_dir/server/Dashboard" -maxdepth 1 -name '.env*' -print -quit | grep -q .; then
  echo 'FAIL: remove Dashboard .env files before building this release' >&2
  exit 1
fi
while IFS= read -r variable; do
  case "$variable" in VITE_*) echo 'FAIL: unset VITE_* build variables' >&2; exit 1 ;; esac
done < <(compgen -e)
release_dir="$script_dir/releases/$actual_commit"
[[ ! -e "$release_dir" ]] || { echo 'FAIL: release already exists; preserve it' >&2; exit 1; }
cd "$repo_dir/server/Dashboard"
npm ci
npm test
npm run build
# Stage into a unique directory; publish only a complete build.
cd "$script_dir"
mkdir -p releases
staging_dir="$(mktemp -d "releases/.build-XXXXXXXX")"
cp -R "$repo_dir/server/Dashboard/dist/." "$staging_dir/"
printf '%s\n' "$actual_commit" > "$staging_dir/BUILD_COMMIT.txt"
(cd "$staging_dir" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
mv -- "$staging_dir" "releases/$actual_commit"
printf 'PASS: release prepared at %s\n' "$release_dir"
