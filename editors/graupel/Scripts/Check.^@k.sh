#!/bin/sh
# %%%{CotEditorXInput=AllText}%%%
# %%%{CotEditorXOutput=Discard}%%%
# Parses the current document without running it. Errors appear in the Console
# as file:line:col: message. Uses the editor text, so unsaved changes are included. Control-Command-K.
dir=$(mktemp -d)
tmp=$dir/doc.bor
trap 'rm -rf "$dir"' EXIT
cat > "$tmp"
if out=$(~/.local/bin/boreas check "$tmp" 2>&1); then
  echo "${1:-untitled}: no errors" >&2
else
  echo "$out" | sed "s|$tmp|${1:-untitled}|g" >&2
  exit 1
fi
