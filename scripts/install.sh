#!/bin/sh
# Explicit per-user installation; never start DeskDeck or a login item.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
source_dir=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
cd "$source_dir"
exec "${DESKDECK_PYTHON:-python3}" -B -m deskdeck.installer install --source "$source_dir" "$@"
