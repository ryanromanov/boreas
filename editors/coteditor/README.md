# Graupel and CotEditor support

For [Graupel](https://github.com/ryanromanov/graupel) or CotEditor 7 or later
(`brew install --cask coteditor`). CotEditor 7 replaced the YAML syntax format with
`.cotsyntax` packages, so the syntax here is a package.

    ./install.sh

Copies the scripts into the Scripts folder of each app that has one. Graupel already includes
the syntax. In CotEditor it has to be imported by hand (CotEditor is sandboxed, so a script cannot
write into its Syntaxes folder): Settings > Format, the "..." menu under the syntax list, Import,
then pick `Boreas.cotsyntax`. `.bor` files pick up the Boreas syntax
(highlighting, outline of procedures, modules and records, `!` comments). In Graupel, pressing Return
after a line that opens a block (`program`, `function`, `do`, `if ... then`, `case`, and so on) also indents the
next line; CotEditor ignores those rules, since they are regular expressions.

Scripts (Script menu > Boreas), all using `~/.local/bin/boreas`:

- Run, Command-R: runs the document (unsaved edits included), output in the Console.
- Check, Control-Command-K (Command-K is Quick Actions): `boreas check`, errors in the Console as `file:line:col: message`.
- Convert Fortran File to Boreas, Control-Command-C: `boreas convert --verify` on the saved file.

A shortcut that the app already uses is ignored; rename the script file (`Run.@r.sh`:
`@` is Command, `$` Shift, `~` Option, `^` Control) to pick another.
