"""`boreas fmt`: re-indent Boreas source by its block structure.

Only the whitespace at the start and end of each line changes. Everything else stays as written: comments,
blank lines, `&` line breaks, the spacing inside a line, and the capitalisation of names. A line's depth comes
from the first words of its statement, read with the lexer's own token rules, so text inside strings and
comments is never mistaken for a keyword:

- `program`, `module`, `record`, `interface`, and `function` / `subroutine` definitions open a block (the
  signatures listed inside an `interface` don't);
- `if` / `else` / `end if` (always a block; `then` may follow a select expression lines later), and `do`
  (with or without a label);
- `select case` and `select type`: a `case` line sits one level inside the select, its body one level deeper;
- a select expression (`x = select case (n)` ... `case 1 -> ...` ... `end select`): its arms sit one level in.

Lines that continue a statement after `&` keep how far they were indented past the statement's first line.
Formatting works on code that doesn't parse, as long as it lexes; unbalanced `end` lines stop at depth 0.
"""
import re
from dataclasses import dataclass, field
from .lexer import LangError, TOKEN_RE

DEFAULT_INDENT = 2


@dataclass
class _Line:
    text: str                 # the line without its line ending
    ending: str               # "\n", "\r\n", or "" for a last line without one
    words: list = field(default_factory=list)    # the tokens on this line, lowercased, as (kind, text)
    continued: bool = False   # ends with `&`: the statement goes on on the next line


@dataclass
class _Frame:
    kind: str                 # "block", "interface", "if", "do", "select", "select-expr"
    depth: int                # the depth of the line that opened it (and of its `end` line)
    in_case: bool = False     # a select statement: a `case` line has been seen, so bodies are one deeper


def _split(src):
    """The physical lines of `src`, with each one's tokens and whether it continues on the next line."""
    lines = []
    for m in re.finditer(r"([^\n]*)(\n|$)", src):
        if m.start() == len(src) and lines:
            break
        text, ending = m.group(1), m.group(2)
        if text.endswith("\r") and ending:
            text, ending = text[:-1], "\r\n"
        lines.append(_Line(text, ending))
    # tokens, read the way the lexer reads them (strings and comments can't span lines, so per line is exact)
    for number, line in enumerate(lines, 1):
        pos, text = 0, line.text + "\n"
        while pos < len(text):
            m = TOKEN_RE.match(text, pos)
            if not m:
                err = LangError(f"line {number}: unexpected character {text[pos]!r}")
                err.col = pos + 1
                raise err
            kind, word = m.lastgroup, m.group()
            pos = m.end()
            if kind == "cont":
                line.continued = True
            elif kind not in ("ws", "comment", "nl"):
                line.words.append((kind, word.lower()))
    return lines


def _statements(lines):
    """Group physical lines into statements: a line ending in `&` goes on with the next one."""
    group = []
    for line in lines:
        group.append(line)
        if not line.continued:
            yield group
            group = []
    if group:
        yield group


def _width(text, tab):
    """The width of a run of spaces and tabs, a tab counting as `tab` columns."""
    return sum(tab if c == "\t" else 1 for c in text)


def _leading(text):
    return text[:len(text) - len(text.lstrip(" \t"))]


def format_source(src, indent=DEFAULT_INDENT, tabs=False):
    """Returns `src` re-indented, `indent` spaces a level, or a tab a level when `tabs`.
    Raises LangError if the source has a character Boreas can't read."""
    lines, formatted = _format_lines(src, indent, tabs)
    return "".join(text + line.ending for line, text in zip(lines, formatted))


def _format_lines(src, indent, tabs):
    """The physical lines of `src`, and the formatted text of each one (without its line ending). Formatting
    never adds or removes a line, so the two lists match one to one."""
    lines = _split(src)
    unit = "\t" if tabs else " " * indent
    tab_width = indent if indent > 0 else DEFAULT_INDENT
    stack = []
    out = []

    def body_depth():
        if not stack:
            return 0
        top = stack[-1]
        return top.depth + (2 if top.kind == "select" and top.in_case else 1)

    for group in _statements(lines):
        first = group[0]
        words = [w for line in group for w in line.words]
        texts = [t for _, t in words]
        head = words[1:] if texts[:1] == ["public"] else words     # `public function ...` in a module
        kinds = [k for k, _ in head]
        head = [t for _, t in head]
        word0 = head[0] if head else None
        word1 = head[1] if len(head) > 1 else None
        top = stack[-1] if stack else None

        # the depth of this statement's first line
        if not words:                                   # a blank line or a comment
            depth = body_depth()
        elif word0 == "end":
            depth = stack.pop().depth if stack else 0
        elif word0 == "else" and top and top.kind == "if":
            depth = top.depth
        elif word0 == "case" and top and top.kind in ("select", "select-expr"):
            depth = top.depth + 1
            top.in_case = True
        else:
            depth = body_depth()

        # does it open a block?
        labeled_do = len(head) > 2 and kinds[0] == "ident" and head[1] == ":" and head[2] == "do"
        if word0 in ("program", "module", "record"):
            stack.append(_Frame("block", depth))
        elif word0 == "interface":
            stack.append(_Frame("interface", depth))
        elif (word0 in ("function", "subroutine") and len(head) > 1 and kinds[1] == "ident"
              and not (top and top.kind == "interface")):
            stack.append(_Frame("block", depth))        # a definition, not a procedure type like `function(real)`
        elif word0 == "if":                             # always a block: Boreas has no one-line if, and `then`
            stack.append(_Frame("if", depth))           # can come lines later, after a select expression
        elif word0 == "do" or labeled_do:
            stack.append(_Frame("do", depth))
        elif word0 == "select" and word1 in ("case", "type"):
            stack.append(_Frame("select", depth))
        # a select expression can start later in the line, even after one ends: `end select, 1 + select case n`
        for i in range(1, len(texts) - 1):
            if texts[i] == "select" and texts[i + 1] == "case":
                stack.append(_Frame("select-expr", depth))

        # write the lines, keeping how far continuation lines sit past the first
        first_width = _width(_leading(first.text), tab_width)
        for i, line in enumerate(group):
            content = line.text.strip(" \t")
            if not content:
                out.append("")
                continue
            if i == 0:
                prefix = unit * depth
            else:
                offset = _width(_leading(line.text), tab_width) - first_width
                prefix = unit * depth + (" " * offset if offset > 0 else unit)
            out.append(prefix + content)
    return lines, out


def formatting_edits(src, options=None):
    """The LSP answer to `textDocument/formatting`: for each line that changes, an edit replacing only the
    whitespace at its start and the whitespace at its end, so an editor keeps the rest of the text (and the
    selection and scroll position in it) as it is. No edits when nothing changes. `options` may give `tabSize`
    and `insertSpaces`."""
    options = options or {}
    size = options.get("tabSize")
    indent = size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else DEFAULT_INDENT
    tabs = options.get("insertSpaces") is False
    lines, formatted = _format_lines(src, indent, tabs)
    edits = []
    for number, (line, new) in enumerate(zip(lines, formatted)):
        old = line.text
        if old == new:
            continue
        def edit(start, end, text):
            edits.append({"range": {"start": {"line": number, "character": start},
                                    "end": {"line": number, "character": end}}, "newText": text})
        if not new:                                     # a line of only spaces and tabs (all ASCII)
            edit(0, len(old), "")
            continue
        old_lead, new_lead = _leading(old), _leading(new)
        if old_lead != new_lead:                        # spaces and tabs: one UTF-16 unit each
            edit(0, len(old_lead), new_lead)
        trimmed = old.rstrip(" \t")
        if len(trimmed) < len(old):
            start = _utf16(trimmed)
            edit(start, start + len(old) - len(trimmed), "")
    return edits


def _utf16(text):
    """The length of `text` in UTF-16 code units, the unit LSP positions count in."""
    return len(text.encode("utf-16-le")) // 2
