import re
from dataclasses import dataclass


class LangError(Exception):
    pass


@dataclass
class Token:
    kind: str  # int, real, str, ident, op, nl, eof
    text: str
    line: int


TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t\r]+)
  | (?P<comment>!.*)
  | (?P<cont>&[ \t]*\n)
  | (?P<nl>\n)
  | (?P<real>\d+\.\d*(?:[eE][+-]?\d+)?|\.\d+|\d+[eE][+-]?\d+)
  | (?P<int>\d+)
  | (?P<str>"[^"\n]*")
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>::|\*\*|->|==|/=|<=|>=|[-+*/<>=(),:\[\]])
""", re.X)


def lex(src):
    tokens, pos, line = [], 0, 1
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise LangError(f"line {line}: unexpected character {src[pos]!r}")
        kind, text = m.lastgroup, m.group()
        pos = m.end()
        if kind in ("ws", "comment"):
            continue
        if kind == "cont":
            line += 1
            continue
        if kind == "nl":
            if tokens and tokens[-1].kind != "nl":
                tokens.append(Token("nl", "\n", line))
            line += 1
            continue
        if kind == "ident":
            text = text.lower()  # the language is case-insensitive
        tokens.append(Token(kind, text, line))
    tokens.append(Token("nl", "\n", line))
    tokens.append(Token("eof", "", line))
    return tokens
