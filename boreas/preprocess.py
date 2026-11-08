"""The C-style preprocessor lines some Fortran carries (`#if`, `#ifdef`, `#define`, `#include`, ...).

The converter does what a preprocessing build would: it keeps the branch the conditions pick, given the
macros defined with `--define` and by `#define` lines in the file; a name that is not defined counts as
undefined. Directive lines and the branches not taken become blank lines, so line numbers stay the
same (a file pulled in by `#include` is the exception: its lines shift what follows). A condition that
cannot be evaluated keeps its first branch and is a TODO."""
import os
import re

MAX_INCLUDE_DEPTH = 8
DIRECTIVE = re.compile(r"^\s*#\s*([A-Za-z_]+)\s*(.*)$")
IDENT = re.compile(r"[A-Za-z_]\w*")
QUOTED_OR_COMMENT = re.compile(r"""'[^']*'|"[^"]*"|!.*$""")
TOKEN = re.compile(r"\s*(0[xX][0-9a-fA-F]+|\d+|[A-Za-z_]\w*|&&|\|\||==|!=|<=|>=|<<|>>|[-+*/%<>!~&|^?:()])")


class Unevaluable(Exception):
    pass


def has_directives(text):
    return any(DIRECTIVE.match(line) for line in text.split("\n"))


def preprocess(text, defines=None, base_dir=None):
    """(text without directives, findings); `defines` maps macro names to their text (`"1"` for a bare -D)."""
    if not has_directives(text):
        return text, []
    p = Preprocessor(dict(defines or {}), base_dir)
    out = p.run(text, base_dir, 0, 0)
    given = ", ".join(sorted(defines or {})) or "none"
    p.findings.insert(0, (p.first, "note", f"preprocessor lines were evaluated with these macros defined: {given} "
                                           f"(`--define NAME` defines one)"))
    return "\n".join(out), p.findings


class Preprocessor:
    def __init__(self, macros, base_dir):
        self.macros = macros
        self.base_dir = base_dir
        self.findings = []
        self.first = 0

    def todo(self, line, msg):
        self.findings.append((line, "todo", msg))

    def run(self, text, base_dir, depth, outer_line):
        lines = text.split("\n")
        out, stack, i = [], [], 0          # stack entries: [outer active, a branch was taken, this one active]
        while i < len(lines):
            m = DIRECTIVE.match(lines[i])
            if m is None:
                out.append(self.expand(lines[i]) if all(s[2] for s in stack) else "")
                i += 1
                continue
            start, text_ = i, m.group(2)
            while text_.endswith("\\") and i + 1 < len(lines):      # a continued directive
                i += 1
                text_ = text_[:-1] + " " + lines[i].strip()
            out.extend([""] * (i - start + 1))
            line = start + 1 if depth == 0 else outer_line
            self.first = self.first or line
            word, active = m.group(1), all(s[2] for s in stack)
            self.directive(word, text_.strip(), line, stack, active, out, base_dir, depth)
            i += 1
        for _ in stack:
            self.todo(self.first, "an #if block is never closed with #endif")
        return out

    def directive(self, word, rest, line, stack, active, out, base_dir, depth):
        if word in ("if", "ifdef", "ifndef"):
            if not active:
                stack.append([False, True, False])
                return
            if word == "if":
                take = self.condition(rest, line)
            else:
                name = rest.split()[0] if rest.split() else ""
                take = (name in self.macros) == (word == "ifdef")
            stack.append([True, take, take])
        elif word in ("elif", "else"):
            if not stack:
                self.todo(line, f"#{word} without an #if")
                return
            top = stack[-1]
            if not top[0] or top[1]:
                top[2] = False
            elif word == "else":
                top[1] = top[2] = True
            else:
                top[2] = self.condition(rest, line)
                top[1] = top[2]
        elif word == "endif":
            if not stack:
                self.todo(line, "#endif without an #if")
            else:
                stack.pop()
        elif not active:
            return
        elif word == "define":
            self.define(rest, line)
        elif word == "undef":
            self.macros.pop(rest.split()[0] if rest.split() else "", None)
        elif word == "include":
            self.include(rest, line, out, base_dir, depth)
        elif word == "error":
            self.todo(line, f"#error: {rest}")

    def define(self, rest, line):
        m = IDENT.match(rest)
        if m is None:
            self.todo(line, f"cannot read this #define: {rest}")
            return
        name, after = m.group(0), rest[m.end():]
        if after.startswith("("):
            self.todo(line, f"the function-like macro '{name}' is not supported; its uses will not convert")
            return
        self.macros[name] = after.strip()

    def include(self, rest, line, out, base_dir, depth):
        m = re.match(r'"([^"]+)"', rest)
        if m is None or base_dir is None or depth >= MAX_INCLUDE_DEPTH:
            self.todo(line, f"#include {rest} is not expanded (only a quoted file in the same directory is)")
            return
        path = os.path.join(base_dir, m.group(1))
        try:
            with open(path, errors="replace") as f:
                text = f.read()
        except OSError:
            self.todo(line, f"#include {rest}: the file '{m.group(1)}' was not found next to the source")
            return
        out.extend(self.run(text.rstrip("\n"), os.path.dirname(path), depth + 1, line))

    def expand(self, line):
        """Replace macro names that have a value, leaving strings and comments alone."""
        if not any(v for v in self.macros.values()):
            return line
        pieces, last = [], 0
        for q in QUOTED_OR_COMMENT.finditer(line):
            pieces.append(self.substitute(line[last:q.start()]))
            pieces.append(q.group(0))
            last = q.end()
        pieces.append(self.substitute(line[last:]))
        return "".join(pieces)

    def substitute(self, code):
        return IDENT.sub(lambda m: self.macros.get(m.group(0)) or m.group(0), code)

    def condition(self, text, line):
        try:
            return bool(self.evaluate(text, 0))
        except Unevaluable as e:
            self.todo(line, f"cannot evaluate '#if {text}' ({e}); its first branch was kept")
            return True

    # -- expressions
    def evaluate(self, text, depth):
        if depth > 10:
            raise Unevaluable("a macro refers to itself")
        tokens, pos = [], 0
        text = text.split("//")[0].split("/*")[0]
        while pos < len(text) and text[pos:].strip():
            m = TOKEN.match(text, pos)
            if m is None:
                raise Unevaluable(f"unexpected text '{text[pos:].strip()[:12]}'")
            tokens.append(m.group(1))
            pos = m.end()
        if not tokens:
            raise Unevaluable("no condition")
        value, rest = self.ternary(tokens, depth)
        if rest:
            raise Unevaluable(f"unexpected '{rest[0]}'")
        return value

    BINARY = [["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="], ["<", "<=", ">", ">="], ["<<", ">>"],
              ["+", "-"], ["*", "/", "%"]]

    def ternary(self, t, depth):
        cond, t = self.binary(t, 0, depth)
        if t and t[0] == "?":
            a, t = self.ternary(t[1:], depth)
            if not t or t[0] != ":":
                raise Unevaluable("a '?' without ':'")
            b, t = self.ternary(t[1:], depth)
            return (a if cond else b), t
        return cond, t

    def binary(self, t, level, depth):
        if level == len(self.BINARY):
            return self.unary(t, depth)
        left, t = self.binary(t, level + 1, depth)
        while t and t[0] in self.BINARY[level]:
            op = t[0]
            right, t = self.binary(t[1:], level + 1, depth)
            left = self.apply(op, left, right)
        return left, t

    @staticmethod
    def apply(op, a, b):
        try:
            return {"||": lambda: int(bool(a or b)), "&&": lambda: int(bool(a and b)), "|": lambda: a | b,
                    "^": lambda: a ^ b, "&": lambda: a & b, "==": lambda: int(a == b), "!=": lambda: int(a != b),
                    "<": lambda: int(a < b), "<=": lambda: int(a <= b), ">": lambda: int(a > b),
                    ">=": lambda: int(a >= b), "<<": lambda: a << b, ">>": lambda: a >> b, "+": lambda: a + b,
                    "-": lambda: a - b, "*": lambda: a * b,
                    "/": lambda: int(a / b), "%": lambda: a - b * int(a / b)}[op]()
        except (ZeroDivisionError, ValueError):
            raise Unevaluable("division by zero")

    def unary(self, t, depth):
        if not t:
            raise Unevaluable("the condition ends early")
        head, t = t[0], t[1:]
        if head in ("!", "~", "-", "+"):
            v, t = self.unary(t, depth)
            return {"!": int(not v), "~": ~v, "-": -v, "+": v}[head], t
        if head == "(":
            v, t = self.ternary(t, depth)
            if not t or t[0] != ")":
                raise Unevaluable("a missing ')'")
            return v, t[1:]
        if head == "defined":
            paren = bool(t) and t[0] == "("
            if paren:
                t = t[1:]
            if not t or not IDENT.fullmatch(t[0]):
                raise Unevaluable("'defined' without a name")
            name, t = t[0], t[1:]
            if paren:
                if not t or t[0] != ")":
                    raise Unevaluable("a missing ')'")
                t = t[1:]
            return int(name in self.macros), t
        if head[0].isdigit():
            return int(head, 16 if head[:2].lower() == "0x" else 10), t
        if IDENT.fullmatch(head):
            if t and t[0] == "(":
                raise Unevaluable(f"a call of '{head}'")
            if head not in self.macros:
                return 0, t
            return self.evaluate(self.macros[head], depth + 1), t
        raise Unevaluable(f"unexpected '{head}'")
