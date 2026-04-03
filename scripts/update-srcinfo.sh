#!/usr/bin/env sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR/packaging"

if ! command -v makepkg >/dev/null 2>&1; then
  echo "makepkg is required to regenerate .SRCINFO" >&2
  exit 1
fi

makepkg --printsrcinfo > .SRCINFO
echo "Updated packaging/.SRCINFO"
