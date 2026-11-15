#!/usr/bin/env bash
# Install into the default profile only, then perform exactly one operator-requested review.
# No sudo, package installs, credential reads, gateway changes, or automatic model-call retries.
set -euo pipefail
umask 077
if [[ $# != 3 ]]; then
  printf 'Usage: bash %s IMAGE_ROOT HOST_IMAGE NEW_JSON_REPORT\n' "$0" >&2
  exit 2
fi
root=$1
image=$2
output=$3
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/default-image-review"
profile_home=${HERMES_HOME:-"$HOME/.hermes"}
if [[ "$profile_home" != "$HOME/.hermes" ]]; then
  printf 'Refusing to modify a non-default profile. Run in the default Hermes profile.\n' >&2
  exit 2
fi
export HERMES_HOME="$profile_home"
command -v hermes >/dev/null
command -v python3 >/dev/null
[[ -d "$root" && -f "$image" && ! -e "$output" && ! -L "$output" ]] || {
  printf 'Image root/image must exist; report must be a new path.\n' >&2; exit 2;
}
python3 -c 'import pathlib,sys; r,p=map(pathlib.Path,sys.argv[1:]); assert r.is_absolute() and p.is_absolute() and p.is_relative_to(r) and len(r.parts)>=3 and ".." not in p.parts and ".." not in r.parts' "$root" "$image"
root_json=$(python3 -c 'import json,sys; print(json.dumps([sys.argv[1]]))' "$root")
target="$profile_home/plugins/default-image-review"
# Preserve unexpected files and refuse symlink destinations.
for path in "$profile_home" "$profile_home/plugins" "$target"; do
  [[ ! -L "$path" ]] || { printf 'Refusing symlink plugin destination.\n' >&2; exit 2; }
done
for name in __init__.py plugin.yaml; do
  [[ -f "$source_dir/$name" && ! -L "$source_dir/$name" ]] || exit 2
  if [[ -e "$target/$name" || -L "$target/$name" ]]; then
    [[ ! -L "$target/$name" ]] && cmp -s -- "$source_dir/$name" "$target/$name" || {
      printf 'Existing plugin differs; refusing overwrite.\n' >&2; exit 2;
    }
  fi
done
before_aux=$(hermes config get auxiliary.vision.model)
mkdir -p -- "$target"
for name in __init__.py plugin.yaml; do
  if [[ ! -e "$target/$name" ]]; then
    cp -n -- "$source_dir/$name" "$target/$name"
  fi
  cmp -s -- "$source_dir/$name" "$target/$name"
done
hermes config set plugins.entries.default-image-review.settings.image_roots "$root_json"
# Required even to pin overrides to the persisted main route; tool callers cannot supply overrides.
hermes config set plugins.entries.default-image-review.llm.allow_model_override true
hermes config set plugins.entries.default-image-review.llm.allow_provider_override true
hermes plugins enable default-image-review
after_aux=$(hermes config get auxiliary.vision.model)
[[ "$before_aux" == "$after_aux" ]] || {
  printf 'Auxiliary vision changed unexpectedly; refusing model call.\n' >&2; exit 1;
}
hermes review-image --image "$image" \
  --question 'Identify the vehicles visible in this driveway image. Distinguish vehicle type from make/model guesses. State uncertainty and do not use previous descriptions.' \
  --output "$output"
printf 'Installed plugin and wrote one host review. Gateway not restarted.\n'
printf 'To disable: hermes plugins disable default-image-review\n'
