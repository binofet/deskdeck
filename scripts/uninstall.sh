#!/bin/sh
# Use an external interpreter, never the installed venv being removed.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
source_dir=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
cd "$source_dir"
exec "${DESKDECK_PYTHON:-python3}" -B -m deskdeck.installer uninstall "$@"
