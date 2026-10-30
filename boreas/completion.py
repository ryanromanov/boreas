"""Completion for `boreas lsp`: the keywords, types, built-ins and the names declared in the
document that start with the word before the cursor. The document is usually half-typed when
completion is asked for, so the names are found by scanning its lines, not by parsing it."""
import re

from .interp import BUILTINS, ELEMENTAL
from .parser import KEYWORDS, TYPES

# LSP CompletionItemKind values
FUNCTION, VARIABLE, MODULE, KEYWORD, STRUCT, TYPE = 3, 6, 9, 14, 22, 25

STRING_RE = re.compile(r'"[^"\n]*"?|\'[^\'\n]*\'?')
PROCEDURE_RE = re.compile(r"(?:(?:public|private)\s+)?(function|subroutine)\s+([A-Za-z_]\w*)\s*(\(.*)?$", re.I)
END_RE = re.compile(r"end\s+(function|subroutine|program)\b", re.I)
BLOCK_RE = re.compile(r"(?:(?:public|private)\s+)?(record|module)\s+([A-Za-z_]\w*)\s*$", re.I)
PROGRAM_RE = re.compile(r"program\b", re.I)
DECL_RE = re.compile(r"(?:\b(?:in|out|inout)\s+)?([A-Za-z_]\w*\??(?:\s*,\s*allocatable)?)\s*::\s*([A-Za-z_]\w*)", re.I)


def code_part(line):
    """The line without its strings and comment, so that nothing in them looks like code."""
    return STRING_RE.sub('""', line).split("!", 1)[0]


def declarations(lines):
    """The names the lines declare, as [(name, kind, detail, scope)], and the fields of each
    record, as {record name: [(field, type)]}. `scope` is the index of the line that starts the
    enclosing procedure or program, or None for top-level names."""
    found, records, scope, record = [], {}, None, None
    for i, raw in enumerate(lines):
        line = code_part(raw).strip()
        if record is not None:            # fields are reached through a record value
            if re.match(r"end\s+record\b", line, re.I):
                record = None
            else:
                record.extend((name, t) for t, name in DECL_RE.findall(line))
            continue
        if END_RE.match(line):
            scope = None
            continue
        if m := PROCEDURE_RE.match(line):
            found.append((m.group(2), FUNCTION, line, None))
            scope = i
            for t, name in DECL_RE.findall(m.group(3) or ""):
                found.append((name, VARIABLE, t, scope))
            continue
        if PROGRAM_RE.match(line):
            scope = i
            continue
        if m := BLOCK_RE.match(line):
            is_record = m.group(1).lower() == "record"
            found.append((m.group(2), STRUCT if is_record else MODULE, m.group(1).lower(), None))
            if is_record:
                record = records.setdefault(m.group(2).lower(), [])
            continue
        for t, name in DECL_RE.findall(line):
            found.append((name, VARIABLE, t, scope))
    return found, records


def base_type(t):
    """The record or type name of a declared type such as `point?` or `real, allocatable`."""
    return re.match(r"\w*", t).group().lower()


def fields(chain, variables, records):
    """The fields of the record that a chain like `s.to` or `pts(i)` names, or None."""
    parts = [re.sub(r"\(.*", "", p).strip().lower() for p in chain.split(".")]
    t = variables.get(parts[0])
    for part in parts[1:]:
        t = dict((f.lower(), ft) for f, ft in records.get(base_type(t or ""), [])).get(part)
    return records.get(base_type(t or ""))


def in_string(before):
    """Whether the text before the cursor ends inside a string."""
    last = None
    for last in STRING_RE.finditer(before):
        pass
    return bool(last) and last.end() == len(before) and (len(last.group()) == 1 or last.group()[-1] != last.group()[0])


def enclosing_scope(lines, row):
    """The index of the line that starts the procedure or program around `row`, if any."""
    for i in range(min(row, len(lines) - 1), -1, -1):
        line = code_part(lines[i]).strip()
        if END_RE.match(line) and i < row:
            return None
        if PROCEDURE_RE.match(line) or PROGRAM_RE.match(line):
            return i
    return None


def completions(src, line, character):
    """The LSP completion items at a position (0-based line, UTF-16 character)."""
    lines = src.split("\n")
    if not 0 <= line < len(lines):
        return []
    before = lines[line].encode("utf-16-le")[:character * 2].decode("utf-16-le", "ignore")
    if in_string(before) or code_part(before) != STRING_RE.sub('""', before):   # or in a comment
        return []
    prefix = re.search(r"[A-Za-z_]\w*$", before)
    prefix = prefix.group().lower() if prefix else ""
    scope = enclosing_scope(lines, line)
    found, records = declarations(lines)
    items, seen = [], set()

    def add(label, kind, detail=None):
        key = label.lower()
        if key in seen or not key.startswith(prefix) or key == prefix:
            return
        seen.add(key)
        # clients sort by sortText, else by label: keep this order, the document's names first
        items.append({"label": label, "kind": kind, **({"detail": detail} if detail else {}),
                      "sortText": "%04d" % len(items)})

    visible = [d for d in found if d[3] is None or d[3] == scope]

    # a record field after a dot
    if m := re.search(r"([A-Za-z_]\w*(?:\([^()]*\))?(?:\.[A-Za-z_]\w*(?:\([^()]*\))?)*)\.$", before[:len(before) - len(prefix)]):
        variables = {name.lower(): detail for name, kind, detail, _ in visible if kind == VARIABLE}
        for name, t in fields(m.group(1), variables, records) or []:
            add(name, VARIABLE, t)
        return items
    if before[:len(before) - len(prefix)].endswith("."):     # after a number, say
        return []

    for name, kind, detail, _ in visible:
        add(name, kind, detail)
    for name in sorted(TYPES):
        add(name, TYPE, "type")
    for name in sorted(set(BUILTINS) | set(ELEMENTAL)):
        add(name, FUNCTION, "built-in")
    for name in sorted(KEYWORDS):
        add(name, KEYWORD, "keyword")
    return items
