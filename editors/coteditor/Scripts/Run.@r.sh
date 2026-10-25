#!/bin/sh
# %%%{CotEditorXInput=AllText}%%%
# %%%{CotEditorXOutput=NewDocument}%%%
# Runs the current document with Boreas; the output opens in a new document.
# Uses the editor text, so unsaved changes are included. Command-R.
dir=$(mktemp -d)
tmp=$dir/doc.bor
trap 'rm -rf "$dir"' EXIT
cat > "$tmp"
~/.local/bin/boreas "$tmp" 2>&1 | sed "s|$tmp|${1:-untitled}|g"
