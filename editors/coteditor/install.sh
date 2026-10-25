#!/bin/sh
# Install the Boreas scripts for CotEditor 7 or later and explain the syntax import.
# CotEditor 7 replaced the old YAML syntax format with .cotsyntax packages.
set -e
here=$(cd "$(dirname "$0")" && pwd)
# The syntax cannot be copied from a script: the App Store/cask build is sandboxed and macOS
# blocks writes into its container. Import it from CotEditor's settings instead.
echo "syntax: in CotEditor open Settings > Format, use the ... menu Import item,"
echo "        and choose $here/Boreas.cotsyntax"

# Scripts (Script menu > Open Scripts Folder shows where CotEditor looks)
scripts="$HOME/Library/Application Scripts/com.coteditor.CotEditor"
if [ -d "$scripts" ]; then
  mkdir -p "$scripts/Boreas"
  cp "$here"/Scripts/*.sh "$scripts/Boreas/"
  echo "installed scripts: $scripts/Boreas"
else
  echo "scripts folder not found; in CotEditor use Script > Open Scripts Folder and copy Scripts/*.sh there"
fi
