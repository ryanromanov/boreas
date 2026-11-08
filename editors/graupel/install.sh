#!/bin/sh
# Install the Boreas scripts for Graupel. Graupel already includes the Boreas syntax.
set -e
here=$(cd "$(dirname "$0")" && pwd)

# Script menu > Open Scripts Folder shows where Graupel looks.
scripts="$HOME/Library/Application Scripts/com.ryanromanov.Graupel"
if [ -d "$scripts" ]; then
  mkdir -p "$scripts/Boreas"
  rm -f "$scripts/Boreas/Run.@r.sh"      # older installs: Graupel 0.2.0 has its own Run (Command-R)
  cp "$here"/Scripts/*.sh "$scripts/Boreas/"
  echo "installed scripts: $scripts/Boreas"
else
  echo "scripts folder not found; in Graupel use Script > Open Scripts Folder and copy Scripts/*.sh there"
fi
