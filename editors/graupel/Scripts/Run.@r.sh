#!/bin/sh
# %%%{CotEditorXInput=AllText}%%%
# %%%{CotEditorXOutput=Discard}%%%
# Runs the current document with Boreas; the output appears in the Console.
# Uses the editor text, so unsaved changes are included. Command-R.
dir=$(mktemp -d)
tmp=$dir/doc.bor
trap 'rm -rf "$dir"' EXIT
cat > "$tmp"
~/.local/bin/boreas "$tmp" 2>&1 | sed "s|$tmp|${1:-untitled}|g" >&2
