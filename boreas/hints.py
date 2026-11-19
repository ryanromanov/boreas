"""Better messages for mistakes made out of habit from Fortran, C, Python and friends. They are only looked up once
a parse has already failed, so a hint never changes what a correct program means."""
import difflib

# Fortran's dotted operators and constants, and what Boreas calls them.
DOTTED = {"eq": "==", "ne": "/=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=", "and": "and", "or": "or",
          "not": "not", "true": "true", "false": "false", "eqv": "==", "neqv": "/="}
# Keywords people run together, and the two words Boreas wants.
RUN_TOGETHER = {"endif": "end if", "enddo": "end do", "elseif": "else if", "elif": "else if",
                "endfunction": "end function", "endsubroutine": "end subroutine", "endprogram": "end program",
                "endmodule": "end module", "endselect": "end select", "endrecord": "end record",
                "endinterface": "end interface", "endwhere": "end where", "selectcase": "select case"}
LOOPS = {"while": "a loop with a condition is `do while cond ... end do`",
         "for": "a counting loop is `do i = 1, n ... end do`"}
KINDS = ("real", "integer", "complex", "logical", "character")
# Names other languages have that Boreas spells differently (used when a name is not declared).
FOREIGN = {"continue": "to go on with the next iteration, write `cycle`", "elif": "write `else if`",
           "elseif": "write `else if`", "print": "write `println(...)` (or `print(...)` to stay on the line)",
           "printf": "write `println(...)`", "length": "the length of a string is `len(s)`, of an array `size(a)`",
           "len_": "", "str": "to turn a value into text, write `text(x)`", "pow": "power is `**`",
           "nil": "the absent value is `null`", "none": "the absent value is `null`", "none_": "",
           "self_": ""}
FOREIGN = {k: v for k, v in FOREIGN.items() if v}


STATEMENT_WORDS = {"println", "print", "write", "writeln", "allocate", "deallocate"}   # not functions, but people misspell them


def lexer_hint(src, pos):
    """The message for an unexpected character at `src[pos]`, or None."""
    c = src[pos]
    nxt = src[pos + 1] if pos + 1 < len(src) else ""
    if c == "&" and nxt == "&":
        return "'&&' is C's; write 'and'"
    if c == "&":
        return "'&' continues a line only at the very end of a line; for logical and, write 'and'"
    if c == "|":
        return "'||' and '|' are C's; write 'or'"
    if c == "%":
        return "there is no '%' operator: the remainder is mod(a, b), and a field is written p.x, not p%x"
    if c == "^":
        return "'^' is not an operator here: power is '**'"
    if c in "\"'":
        return "unterminated string: it needs its closing " + c + " on the same line"
    return None


def suggest(name, candidates):
    """` (did you mean 'println'?)` for a name close to one of the candidates, or ''."""
    close = difflib.get_close_matches(name, sorted(set(candidates) | STATEMENT_WORDS), n=1, cutoff=0.8)
    return f" (did you mean '{close[0]}'?)" if close else ""


def name_hint(name, candidates=()):
    """What to add after "'x' is not declared" or "unknown function 'x'"."""
    if name in FOREIGN:
        return f"; {FOREIGN[name]}"
    return suggest(name, candidates)


def _comment_bang_equals(text):
    """Is there a `!=` outside a string on this source line (where `!` starts a comment, so it would vanish)?"""
    quote = None
    for i, c in enumerate(text):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "!":
            return i if text[i + 1:i + 2] == "=" else None
    return None


def parse_hint(toks, i, msg, src_lines):
    """For a parse error at token `i`: (line, col, message) with a better message, or None."""
    here = toks[i]
    # a run-together keyword at the start of a line, anywhere up to the error
    prev_kind = "nl"
    for k, t in enumerate(toks[:i + 1]):
        if t.kind == "ident" and prev_kind == "nl" and t.text in RUN_TOGETHER:
            after = toks[k + 1] if k + 1 < len(toks) else None
            loose = t.text in ("elseif", "elif")                          # `elseif (x > 0) then`
            if after is None or (after.kind in ("nl", "eof", "ident") or loose) and after.text not in ("=",):
                return t.line, t.col, f"'{t.text}' is written '{RUN_TOGETHER[t.text]}' (two words)"
        prev_kind = t.kind
    line = [(k, t) for k, t in enumerate(toks) if t.line == here.line]
    words = [t.text for _, t in line]
    first = words[0] if words else ""
    at = lambda n: line[n][1] if 0 <= n < len(line) else None
    if first == "print" and len(words) > 1 and words[1] == "*":
        return here.line, at(1).col, "there is no 'print *'; write println(...)"
    if first == "write" and words[1:4] == ["(", "*", ","]:
        return here.line, at(2).col, "there is no 'write(*, *)'; write println(...)"
    if first in LOOPS and msg.startswith("expected"):
        return here.line, at(0).col, f"'{first}' starts no loop here: {LOOPS[first]}"
    for n in range(len(line) - 2):                                     # Fortran's .eq. .and. .true.
        if words[n] == "." and words[n + 2] == "." and words[n + 1] in DOTTED:
            word = words[n + 1]
            use = DOTTED[word]
            return here.line, at(n).col, f"'.{word}.' is Fortran's; write {use!r}" if use not in ("true", "false") \
                else f"'.{word}.' is Fortran's; write {use}"
    bang = _comment_bang_equals(src_lines[here.line - 1]) if 0 < here.line <= len(src_lines) else None
    if bang is not None:
        return here.line, bang + 1, "'!' starts a comment here, so '!=' is cut off; not-equal is '/='"
    depth = 0
    for n, t in enumerate(line):
        k, tok = t
        nxt, prv = at(n + 1), at(n - 1)
        adjacent = nxt is not None and nxt.col == tok.col + len(tok.text)
        if tok.text in ("(", "[", "{"):
            depth += 1
        elif tok.text in (")", "]", "}"):
            depth -= 1
        elif tok.text == ";" and depth == 0:
            return here.line, tok.col, "a statement ends at the end of the line (a ';' only separates matrix rows inside [ ])"
        if tok.text == "<" and nxt is not None and nxt.text == ">" and adjacent:
            return here.line, tok.col, "'<>' is not an operator here; not-equal is '/='"
        if tok.text == "/" and nxt is not None and nxt.text == "/" and adjacent:
            return here.line, tok.col, "'//' is Fortran's string join; join strings with '+'"
        if tok.text == ":" and nxt is not None and nxt.text == "=" and adjacent:
            return here.line, tok.col, "assignment is '=', not ':='"
        if tok.text in "+-*" and tok.kind == "op" and nxt is not None and nxt.text == "=" and adjacent \
                and first not in ("if", "do") and line[0][1].kind == "ident":
            return here.line, tok.col, f"there is no '{tok.text}='; write {first} = {first} {tok.text} ..."
        if tok.text in ("+", "-") and nxt is not None and nxt.text == tok.text and adjacent \
                and (at(n + 2) is None or at(n + 2).kind in ("nl", "eof")) and line[0][1].kind == "ident":
            return here.line, tok.col, f"there is no '{tok.text * 2}'; write {first} = {first} {tok.text} 1"
    if here.text == "*" and i > 0 and toks[i - 1].text in KINDS:
        return here.line, here.col, "write a kind as a type name: real, real32, int8, int16, int32, int"
    if msg == "expected 'then'" and here.text == "=":
        return here.line, here.col, "a condition compares with '=='; a single '=' assigns"
    return None
