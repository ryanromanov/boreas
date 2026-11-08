# Graupel support

[Graupel](https://github.com/ryanromanov/graupel) includes the Boreas syntax (`Boreas.cotsyntax`
here is its source): `.bor` files get highlighting, an outline of procedures, modules and
records, `!` comments, and pressing Return after a line that opens a block (`program`,
`function`, `do`, `if ... then`, `case`, and so on) indents the next line.

    ./install.sh

Copies the scripts into Graupel's Scripts folder.

Scripts (Script menu > Boreas), all using `~/.local/bin/boreas`:

- Running a file is built into Graupel (Run > Run, Command-R; needs Graupel 0.2.0 or later), so there is no Run script.
- Check, Control-Command-K (Command-K is Quick Actions): `boreas check`, errors in the Console as `file:line:col: message`.
- Convert Fortran File to Boreas, Control-Command-C: `boreas convert --verify` on the saved file.

A shortcut that the app already uses is ignored; rename the script file (`Check.^@k.sh`:
`@` is Command, `$` Shift, `~` Option, `^` Control) to pick another.
