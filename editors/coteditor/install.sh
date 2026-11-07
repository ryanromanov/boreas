#!/bin/sh
# Install the Boreas scripts for Graupel and for CotEditor 7 or later, whichever is set up here.
# Both apps read the same scripts: Graupel also accepts the CotEditorX tokens in their headers.
set -e
here=$(cd "$(dirname "$0")" && pwd)

# Script menu > Open Scripts Folder shows where each app looks.
found=
for app in com.ryanromanov.Graupel com.coteditor.CotEditor; do
  scripts="$HOME/Library/Application Scripts/$app"
  [ -d "$scripts" ] || continue
  mkdir -p "$scripts/Boreas"
  cp "$here"/Scripts/*.sh "$scripts/Boreas/"
  echo "installed scripts: $scripts/Boreas"
  found=yes
done
if [ -z "$found" ]; then
  echo "scripts folder not found; in Graupel or CotEditor use Script > Open Scripts Folder and copy Scripts/*.sh there"
fi

# Graupel includes the syntax. CotEditor's cannot be copied from a script: the App Store/cask build
# is sandboxed and macOS blocks writes into its container, so it has to be imported.
echo "syntax: Graupel already includes it. In CotEditor open Settings > Format, use the ... menu"
echo "        Import item, and choose $here/Boreas.cotsyntax"
