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
case "$1" in
  *.[fF]90|*.[fF]95|*.[fF]03|*.[fF]08|*.[fF]18) ;;
  *) echo "Convert Fortran needs a saved free-form Fortran file (.f90 or later), not $1" >&2; exit 1 ;;
esac
# CotEditor's PATH lacks Homebrew, where gfortran lives.
PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
~/.local/bin/boreas convert --verify "$1"
