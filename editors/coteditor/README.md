# CotEditor support

Needs CotEditor 7 or later (`brew install --cask coteditor`). CotEditor 7 replaced the
YAML syntax format with `.cotsyntax` packages, so the syntax here is a package.

    ./install.sh

Copies the scripts into CotEditor's Scripts folder. The syntax has to be imported by hand
(CotEditor is sandboxed, so a script cannot write into its Syntaxes folder): Settings >
Format, the "..." menu under the syntax list, Import, then pick `Boreas.cotsyntax`. `.bor` files pick up the Boreas syntax
(highlighting, outline of procedures, modules and records, `!` comments).

Scripts (Script menu > Boreas), all using `~/.local/bin/boreas`:

- Run, Command-R: runs the document (unsaved edits included), output in a new document.
- Check, Control-Command-K (Command-K is CotEditor's Quick Actions): `boreas check`, errors in the Console as `file:line:col: message`.
- Convert Fortran, Control-Command-C: `boreas convert --verify` on the saved file.

A shortcut that CotEditor already uses is ignored; rename the script file (`Run.@r.sh`:
`@` is Command, `$` Shift, `~` Option, `^` Control) to pick another.
