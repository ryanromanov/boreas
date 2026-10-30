"""Fixed-form Fortran source -> free-form text, one output line per input line.

Columns 1-5 hold a statement label, column 6 marks a continuation line, columns 7-72 hold the
statement, and a `C`, `c`, `*`, `D` or `!` in column 1 makes the whole line a comment. Keeping the
line numbers means the converter's reports still point at the original file."""
from .lexer import LangError

COMMENT_MARKS = "cC*dD!"
LAST_COLUMN = 72


def is_fixed_form(filename):
    return filename.lower().endswith((".f", ".for", ".f77", ".ftn"))


def _fields(raw):
    """(label, continuation?, statement text) for one non-comment line."""
    if "\t" in raw[:6]:                       # tab form: the label, a tab, then the statement
        label, _, rest = raw.partition("\t")
        if rest[:1] in tuple("123456789"):
            return label.strip(), True, rest[1:]
        return label.strip(), False, rest
    raw = raw[:LAST_COLUMN].ljust(6)
    return raw[:5].strip(), raw[5] not in " 0", raw[6:]


def _drop_comment(text):
    from .fortran import split_comment
    return split_comment(text)[0].rstrip()


def fixed_to_free(src):
    out, last = [], None                      # last: index in `out` of the latest statement line
    for number, raw in enumerate(src.splitlines(), 1):
        if not raw.strip():
            out.append("")
        elif raw[0] in COMMENT_MARKS:
            out.append("!" + raw[1:])
        else:
            label, continuation, text = _fields(raw)
            if continuation:
                if last is None:
                    raise LangError(f"line {number}: a continuation line with nothing to continue")
                out[last] = _drop_comment(out[last]) + "&"          # fixed form ignores blanks, so glue exactly
                out.append("&" + text.rstrip())
            else:
                out.append(f"{label} {text}" if label else text)
            last = len(out) - 1
    return "\n".join(out) + "\n"
