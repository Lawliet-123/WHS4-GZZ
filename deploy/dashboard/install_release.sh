#!/usr/bin/env bash
# Installs static files only. Does not touch Nginx, systemd or server data.
set -euo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'FAIL: run with sudo on the VM' >&2; exit 1; }
source_dir="$(realpath -e -- "${1:?Usage: sudo bash install_release.sh RELEASE_DIRECTORY}")"
[[ -f "$source_dir/index.html" && -d "$source_dir/assets" && -f "$source_dir/SHA256SUMS" ]] || { echo 'FAIL: incomplete release' >&2; exit 1; }
release_id="$(cat "$source_dir/BUILD_COMMIT.txt")"
[[ "$release_id" =~ ^[0-9a-f]{40}$ ]] || { echo 'FAIL: invalid release commit' >&2; exit 1; }
# Release directories must contain ordinary files/directories only.
[[ -z "$(find "$source_dir" -mindepth 1 ! -type f ! -type d -print -quit)" ]] || { echo 'FAIL: release contains links or special files' >&2; exit 1; }
(cd "$source_dir" && sha256sum --check --strict SHA256SUMS)
base_dir=/var/www/whs4-dashboard
release_root="$base_dir/releases"
target_dir="$release_root/$release_id"
current_link="$base_dir/current"
[[ ! -L "$base_dir" && ! -L "$release_root" ]] || { echo 'FAIL: installation roots must not be symlinks' >&2; exit 1; }
[[ ! -e "$target_dir" && ! -L "$target_dir" ]] || { echo 'FAIL: installed release already exists; preserve it' >&2; exit 1; }
[[ ! -e "$current_link" || -L "$current_link" ]] || { echo 'FAIL: current exists as a non-symlink' >&2; exit 1; }
install -d -m 755 "$base_dir" "$release_root"
staging_dir="$(mktemp -d "$release_root/.install-XXXXXXXX")"
cp -R -- "$source_dir/." "$staging_dir/"
chown -R root:root "$staging_dir"
find "$staging_dir" -type d -exec chmod 755 {} +
find "$staging_dir" -type f -exec chmod 644 {} +
mv -- "$staging_dir" "$target_dir"
# A unique link is renamed atomically; the old release directory remains.
link_dir="$(mktemp -d "$base_dir/.switch-XXXXXXXX")"
ln -s -- "$target_dir" "$link_dir/current"
mv -Tf -- "$link_dir/current" "$current_link"
rmdir -- "$link_dir"
printf 'PASS: current release is %s\n' "$release_id"
