"""Go to definition and hover for `boreas lsp`. Like completion, it finds declarations by scanning
the document's lines rather than parsing it, so both work while the file is half-typed.

A name resolves to a declaration in the procedure or program around it first, then to a top-level
one: a procedure, record, module, or a module's variable or constant. After a dot, it resolves to
a field of the record that the value before the dot has."""
import re

from .completion import (CHAIN_RE, FUNCTION, MODULE, STRING_RE, STRUCT, VARIABLE, code_part, declarations,
                         enclosing_scope, fields, in_string)
from .interp import BUILTINS, ELEMENTAL

WORD_RE = re.compile(r"[A-Za-z_]\w*")


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def word_at(text, character):
    """The word at a UTF-16 position in a line, as (start, end, word) in Python indexes, or None."""
    index = len(text.encode("utf-16-le")[:character * 2].decode("utf-16-le", "ignore"))
    for m in WORD_RE.finditer(text):
        if m.start() <= index <= m.end():
            return m.start(), m.end(), m.group()
    return None


def masked(line):
    """The line with its strings and comment blanked out, keeping every other character where it is."""
    blanked = STRING_RE.sub(lambda m: " " * len(m.group()), line)
    return blanked.split("!", 1)[0].ljust(len(line))


def name_column(line, name, kind):
    """Where `name` is declared in `line` (a Python index): after `::` for a variable or field,
    after the keyword for a procedure, record or module."""
    code = masked(line)
    word = re.escape(name)
    patterns = [rf"\b(?:function|subroutine|record|module)\s+({word})\b", rf"::\s*({word})\b", rf"\b({word})\b"]
    for pattern in patterns:
        if m := re.search(pattern, code, re.I):
            return m.start(1)
    return 0


def resolve(src, line, character):
    """The declaration of the name at a position (0-based line, UTF-16 character).

    Returns a dict with the word's range on its line, and either `row`, `column` and `text` (the
    declaration) or `builtin` for a built-in function; None if there is no name there or it is not
    declared in the document."""
    lines = src.split("\n")
    if not 0 <= line < len(lines):
        return None
    text = lines[line]
    hit = word_at(text, character)
    if hit is None:
        return None
    start, end, word = hit
    if in_string(text[:start]) or "!" in STRING_RE.sub('""', text[:start]):   # in a string or a comment
        return None
    key = word.lower()
    found, records = declarations(lines)
    scope = enclosing_scope(lines, line)
    result = {"start": start, "end": end, "word": word}

    if m := CHAIN_RE.search(text[:start]):     # a record field after a dot
        visible = [d for d in found if d[3] is None or d[3] == scope]
        variables = {name.lower(): detail for name, kind, detail, *_ in visible if kind == VARIABLE}
        for name, t, row in fields(m.group(1), variables, records) or []:
            if name.lower() == key:
                record = next(r for r, fs in records.items() if any(f[2] == row for f in fs))
                return {**result, "row": row, "column": name_column(lines[row], name, VARIABLE),
                        "name": name, "text": f"{t} :: {name}\n(field of {record})"}
        return None

    for candidates in ([d for d in found if d[3] == scope and scope is not None], [d for d in found if d[3] is None]):
        for name, kind, detail, decl_scope, row in candidates:
            if name.lower() == key:
                return {**result, "row": row, "column": name_column(lines[row], name, kind),
                        "name": name, "text": hover_text(lines, name, kind, detail, decl_scope, row, records)}

    if key in BUILTINS or key in ELEMENTAL:
        return {**result, "builtin": key}
    return None


def hover_text(lines, name, kind, detail, scope, row, records):
    """What hovering over a name shows: its declaration."""
    line = code_part(lines[row]).strip()
    if kind == FUNCTION and scope is None:                 # a procedure: its header
        return line
    if row == scope:                                       # a parameter, declared in the header
        return f"{detail} :: {name}"
    if kind == STRUCT:
        fields_text = "".join(f"\n  {t} :: {f}" for f, t, _ in records.get(name.lower(), []))
        return f"record {name}{fields_text}\nend record"
    if kind == MODULE:
        return f"module {name}"
    return line


def position(lines, row, column):
    return {"line": row, "character": utf16_length(lines[row][:column])}


def definition(src, uri, line, character):
    """The LSP Location of the declaration of the name at a position, or None."""
    found = resolve(src, line, character)
    if not found or "row" not in found:
        return None
    lines = src.split("\n")
    start = position(lines, found["row"], found["column"])
    end = position(lines, found["row"], found["column"] + len(found["name"]))
    return {"uri": uri, "range": {"start": start, "end": end}}


def hover(src, line, character):
    """The LSP Hover for the name at a position, or None."""
    found = resolve(src, line, character)
    if not found:
        return None
    lines = src.split("\n")
    value = (f"built-in function `{found['builtin']}`" if "builtin" in found
             else "```boreas\n" + found["text"] + "\n```")
    return {"contents": {"kind": "markdown", "value": value},
            "range": {"start": position(lines, line, found["start"]), "end": position(lines, line, found["end"])}}
