#!/bin/sh
# Install the Boreas syntax and scripts for CotEditor 7 or later.
# CotEditor 7 replaced the old YAML syntax format with .cotsyntax packages.
set -e
here=$(cd "$(dirname "$0")" && pwd)
app="Library/Application Support/CotEditor"
# The App Store build is sandboxed, so its support folder lives in a container.
if [ -d "$HOME/Library/Containers/com.coteditor.CotEditor/Data/$app" ]; then
  base="$HOME/Library/Containers/com.coteditor.CotEditor/Data/$app"
else
  base="$HOME/$app"
fi
mkdir -p "$base/Syntaxes"
rm -rf "$base/Syntaxes/Boreas.cotsyntax"
cp -R "$here/Boreas.cotsyntax" "$base/Syntaxes/"
echo "installed syntax: $base/Syntaxes/Boreas.cotsyntax"
echo "restart CotEditor, then pick Boreas in the syntax menu (or open a .bor file)"

# Scripts (Script menu > Open Scripts Folder shows where CotEditor looks)
scripts="$HOME/Library/Application Scripts/com.coteditor.CotEditor"
if [ -d "$scripts" ]; then
  mkdir -p "$scripts/Boreas"
  cp "$here"/Scripts/*.sh "$scripts/Boreas/"
  echo "installed scripts: $scripts/Boreas"
else
  echo "scripts folder not found; in CotEditor use Script > Open Scripts Folder and copy Scripts/*.sh there"
fi
