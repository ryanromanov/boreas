#!/bin/sh
# %%%{CotEditorXInput=None}%%%
# %%%{CotEditorXOutput=NewDocument}%%%
# Converts the saved Fortran file to Boreas and checks it against gfortran's output
# (boreas convert --verify). The converted source opens in a new document; the
# report goes to the Console. Control-Command-C.
if [ -z "$1" ]; then
  echo "Save the Fortran file first." >&2
  exit 1
fi
~/.local/bin/boreas convert --verify "$1"
