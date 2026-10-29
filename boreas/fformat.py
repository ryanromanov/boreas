"""Fortran format specifications -> Boreas string expressions.

`write(*,'(A,I5)') s, n` becomes the line `s + right(n, 5)`: the descriptors turn into calls of
right/left/fixed and the literal text into strings. Only the common descriptors are translated
(A, I, F, X, / and literal text, with repeat counts and groups); the rest raise Unsupported."""
from dataclasses import fields, is_dataclass, replace

from .parser import Apply, Binary, Num, Str


class _Space(Str):
    """Blanks from an X descriptor: they move the position, so they are not written at the end of a line."""


class FormatError(Exception):
    """A format we cannot translate (the converter turns it into a TODO)."""


def _scan(text):
    """Split format text into tokens: ('str', s), ('int', n), ('name', s) or ('op', c)."""
    toks, i = [], 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
        elif c in "'\"":
            j, out = i + 1, ""
            while True:
                if j >= len(text):
                    raise FormatError("unterminated string in format")
                if text[j] == c:
                    if text[j + 1:j + 2] == c:
                        out += c
                        j += 2
                        continue
                    break
                out += text[j]
                j += 1
            toks.append(("str", out))
            i = j + 1
        elif c.isdigit():
            j = i
            while j < len(text) and text[j].isdigit():
                j += 1
            toks.append(("int", int(text[i:j])))
            i = j
        elif c.isalpha():
            j = i
            while j < len(text) and text[j].isalpha():
                j += 1
            toks.append(("name", text[i:j].lower()))
            i = j
        elif c in "(),/:.$":
            toks.append(("op", c))
            i += 1
        else:
            raise FormatError(f"unexpected {c!r} in format")
    return toks


class _Parser:
    def __init__(self, toks):
        self.toks, self.i = toks, 0
        self.groups = False

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def accept(self, kind, value=None):
        k, v = self.peek()
        if k == kind and (value is None or v == value):
            self.i += 1
            return True
        return False

    def items(self, closing):
        out = []
        while True:
            if closing and self.accept("op", ")"):
                return out
            if self.peek()[0] is None:
                if closing:
                    raise FormatError("a format is missing its closing parenthesis")
                return out
            out.extend(self.item())
            self.accept("op", ",")                  # the comma between items is optional after a slash

    def number(self):
        k, v = self.peek()
        if k == "int":
            self.i += 1
            return v
        return None

    def item(self):
        k, v = self.peek()
        if k == "str":
            self.i += 1
            return [("lit", v)]
        if (k, v) == ("op", "/"):
            self.i += 1
            return [("slash", 1)]
        if (k, v) == ("op", ":"):
            self.i += 1
            return [("colon",)]
        if (k, v) == ("op", "$"):
            self.i += 1
            return [("nonadvancing",)]
        if (k, v) == ("op", "("):
            self.i += 1
            self.groups = True
            return [("group", 1, self.items(True))]
        repeat = self.number()
        k, v = self.peek()
        if repeat is not None and (k, v) == ("op", "/"):
            self.i += 1
            return [("slash", repeat)]
        if repeat is not None and (k, v) == ("op", "("):
            self.i += 1
            self.groups = True
            return [("group", repeat, self.items(True))]
        if k != "name":
            raise FormatError("unrecognised format item")
        self.i += 1
        if v == "x":
            return [("space", repeat or 1)]
        if v not in ("a", "i", "f"):
            raise FormatError(f"the {v.upper()} edit descriptor is not translated")
        width = self.number()
        digits = None
        if self.accept("op", "."):
            digits = self.number()
            if digits is None:
                raise FormatError("a '.' in a format needs a number after it")
        if v == "f" and digits is None:
            raise FormatError("F needs a number of decimals")
        return [("data", v, width, digits)] * (repeat or 1)


def parse_format(text):
    """The inside of a format's outer parentheses -> (items, has_groups)."""
    p = _Parser(_scan(text))
    items = p.items(False)
    return items, p.groups


def _flatten(items):
    out = []
    for it in items:
        if it[0] == "group":
            for _ in range(it[1]):
                out.extend(_flatten(it[2]))
            if len(out) > 5000:
                raise FormatError("the format repeats too many times")
        else:
            out.append(it)
    return out


def _data(desc, value):
    _, kind, width, digits = desc
    line = value.line if isinstance(value, Apply) else 0
    if kind == "a":
        return value if width is None else Apply("right", [value, Num(width)], line)
    if kind == "i":
        if width is None:
            raise FormatError("I needs a width")
        if digits is None:
            return Apply("right", [value, Num(width)], line)
        if digits > width:
            raise FormatError("the minimum digits of an I edit descriptor exceed its width")
        inner = Apply("right", [value, Num(digits), Str("0")], line)
        return inner if digits == width else Apply("right", [inner, Num(width)], line)
    if width in (None, 0):
        return Apply("fixed", [value, Num(digits)], line)
    return Apply("fixed", [value, Num(digits), Num(width)], line)


def _join(parts):
    """A string expression from literal text and calls; neighbouring text is merged."""
    parts = list(parts)
    while parts and isinstance(parts[-1], _Space):
        parts.pop()
    merged = []
    for p in parts:
        if isinstance(p, Str) and merged and isinstance(merged[-1], Str):
            merged[-1] = Str(merged[-1].value + p.value)
        else:
            merged.append(p)
    if not merged:
        return Str("")
    expr = merged[0]
    for p in merged[1:]:
        expr = Binary("+", expr, p)
    return expr


def render(items, values):
    """Apply a parsed format to the output values. Returns (lines, advance): each line is a Boreas string
    expression, and `advance` is False when the format ends with `$` (no newline after the last line)."""
    has_groups = any(it[0] == "group" for it in items)
    flat = _flatten(items)
    values = list(values)
    lines, current, advance = [], [], True
    n = 0
    while True:
        stopped = False
        for it in flat:
            kind = it[0]
            if kind == "lit":
                current.append(Str(it[1]))
            elif kind == "space":
                current.append(_Space(" " * it[1]))
            elif kind == "slash":
                for _ in range(it[1]):
                    lines.append(current)
                    current = []
            elif kind == "nonadvancing":
                advance = False
            elif kind == "colon":
                if n >= len(values):
                    stopped = True
                    break
            else:
                if n >= len(values):
                    stopped = True
                    break
                current.append(_text(it, values[n]))
                n += 1
        if stopped or n >= len(values):
            break
        if has_groups:
            raise FormatError("the list has more values than the format, which repeats part of it; "
                              "that is not translated")
        if not any(it[0] == "data" for it in flat):
            raise FormatError("the format has no data edit descriptor but there are values to print")
        lines.append(current)                       # more values than descriptors: the format starts again
        current = []
    lines.append(current)
    return [_join(parts) for parts in lines], advance


def _text(desc, value):
    return _data(desc, value)


def substitute(node, name, value):
    """A copy of an expression with every variable `name` replaced by the number `value`."""
    from .parser import Var
    if isinstance(node, Var) and node.name == name:
        return Num(value)
    if isinstance(node, list):
        return [substitute(x, name, value) for x in node]
    if isinstance(node, tuple):
        return tuple(substitute(x, name, value) for x in node)
    if is_dataclass(node) and not isinstance(node, type):
        return replace(node, **{f.name: substitute(getattr(node, f.name), name, value) for f in fields(node)})
    return node
