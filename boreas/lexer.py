import re
from dataclasses import dataclass


class LangError(Exception):
    col = None      # 1-based column, set only by the lexer and parser errors that know it


@dataclass
class Token:
    kind: str  # int, real, str, ident, op, nl, eof
    text: str
    line: int
    col: int = 0


TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t\r]+)
  | (?P<comment>!.*)
  | (?P<cont>&[ \t]*\n)
  | (?P<nl>\n)
  | (?P<real>\d+\.\d*(?:[eE][+-]?\d+)?|\.\d+|\d+[eE][+-]?\d+)
  | (?P<int>\d+)
  | (?P<str>"[^"\n]*"|'[^'\n]*')
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>::|\*\*|->|\?\?|\+%|-%|\*%|==|/=|<=|>=|[-+*/<>=(),:\[\].?])
""", re.X)


def lex(src):
    tokens, pos, line, bol = [], 0, 1, 0   # bol: offset where the current line starts
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            err = LangError(f"line {line}: unexpected character {src[pos]!r}")
            err.col = pos - bol + 1
            raise err
        kind, text, start = m.lastgroup, m.group(), pos
        pos = m.end()
        if kind in ("ws", "comment"):
            continue
        if kind == "cont":
            line += 1
            bol = pos
            continue
        if kind == "nl":
            if tokens and tokens[-1].kind != "nl":
                tokens.append(Token("nl", "\n", line, start - bol + 1))
            line += 1
            bol = pos
            continue
        if kind == "ident":
            text = text.lower()  # the language is case-insensitive
        tokens.append(Token(kind, text, line, start - bol + 1))
    tokens.append(Token("nl", "\n", line))
    tokens.append(Token("eof", "", line))
    return tokens
