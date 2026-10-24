from dataclasses import dataclass, field
from .lexer import lex, LangError

TYPES = {"integer", "real", "logical", "string"}


# --- AST ---------------------------------------------------------------
@dataclass
class Num:  value: object
@dataclass
class Str:  value: str
@dataclass
class Bool: value: bool
@dataclass
class Var:  name: str
@dataclass
class ArrayLit: items: list
@dataclass
class Slice: lo: object; hi: object
@dataclass
class Apply: name: str; args: list; line: int   # index, slice, or call
@dataclass
class Unary: op: str; operand: object
@dataclass
class Binary: op: str; left: object; right: object

@dataclass
class Decl: type: str; name: str; size: object; init: object; line: int
@dataclass
class Assign: target: object; value: object; line: int
@dataclass
class If: branches: list; orelse: list   # branches: [(cond, body)]
@dataclass
class Do: var: str; lo: object; hi: object; step: object; body: list; line: int
@dataclass
class DoWhile: cond: object; body: list
@dataclass
class Print: args: list
@dataclass
class Return: value: object
@dataclass
class Exit: pass
@dataclass
class Cycle: pass
@dataclass
class ExprStmt: expr: object
@dataclass
class Call: name: str; args: list; line: int

@dataclass
class Function:
    """A function or, when is_sub is set, a subroutine."""
    name: str
    params: list          # [(type, name, intent)], intent in {"in", "out", "inout"}
    ret: str | None
    body: list
    is_sub: bool = False
@dataclass
class Program: name: str; body: list
@dataclass
class Unit: functions: dict; program: Program


# --- Parser ------------------------------------------------------------
class Parser:
    def __init__(self, src):
        self.toks, self.i = lex(src), 0

    @property
    def tok(self):
        return self.toks[self.i]

    def error(self, msg):
        raise LangError(f"line {self.tok.line}: {msg} (at {self.tok.text!r})")

    def next(self):
        t = self.tok
        self.i += 1
        return t

    def at(self, *texts):
        return self.tok.kind in ("ident", "op") and self.tok.text in texts

    def accept(self, text):
        if self.at(text):
            return self.next()

    def expect(self, text):
        if not self.at(text):
            self.error(f"expected {text!r}")
        return self.next()

    def ident(self):
        if self.tok.kind != "ident":
            self.error("expected a name")
        return self.next().text

    def newline(self):
        if self.tok.kind not in ("nl", "eof"):
            self.error("expected end of line")
        while self.tok.kind == "nl":
            self.next()

    def skip_newlines(self):
        while self.tok.kind == "nl":
            self.next()

    # -- top level
    def parse(self):
        funcs, prog = {}, None
        self.skip_newlines()
        while self.tok.kind != "eof":
            if self.at("function", "subroutine"):
                f = self.procedure()
                if f.name in funcs:
                    raise LangError(f"'{f.name}' is defined more than once")
                funcs[f.name] = f
            elif self.at("program"):
                if prog:
                    self.error("only one program allowed")
                prog = self.program()
            else:
                self.error("expected 'function', 'subroutine' or 'program'")
            self.skip_newlines()
        if not prog:
            raise LangError("no program found")
        return Unit(funcs, prog)

    def program(self):
        self.expect("program")
        name = self.ident()
        self.newline()
        body = self.block("program")
        self.expect("program")
        if self.tok.kind == "ident":
            self.next()
        return Program(name, body)

    def procedure(self):
        kind = self.next().text           # "function" or "subroutine"
        is_sub = kind == "subroutine"
        name = self.ident()
        params = self.params(is_sub)
        ret = None
        if not is_sub and self.accept("->"):
            ret = self.type_name()
        self.newline()
        body = self.block(kind)
        self.expect(kind)
        if self.tok.kind == "ident":
            self.next()
        return Function(name, params, ret, body, is_sub)

    def params(self, is_sub):
        """`(real :: x, inout integer :: n)`; the mode defaults to `in`."""
        self.expect("(")
        params = []
        if not self.at(")"):
            while True:
                intent = "in"
                if self.at("in", "out", "inout"):
                    intent = self.next().text
                    if intent != "in" and not is_sub:
                        self.error("only subroutines may have out or inout parameters")
                t = self.type_name()
                self.expect("::")
                params.append((t, self.ident(), intent))
                if not self.accept(","):
                    break
        self.expect(")")
        return params

    def type_name(self):
        if self.tok.kind != "ident" or self.tok.text not in TYPES:
            self.error("expected a type (integer, real, logical, string)")
        return self.next().text

    # -- statements
    def block(self, *closers):
        """Parse statements until `end <closer>` / else / etc. Leaves 'end' consumed
        only when followed by one of `closers`."""
        body = []
        self.skip_newlines()
        while True:
            if self.at("end") and self.toks[self.i + 1].text in closers:
                self.next()
                return body
            if self.at("else") and "if" in closers:
                return body
            if self.tok.kind == "eof":
                self.error(f"unexpected end of file, missing 'end {closers[0]}'")
            body.append(self.statement())
            self.skip_newlines()

    def statement(self):
        t = self.tok
        if t.kind == "ident" and t.text in TYPES:
            return self.decl()
        if self.at("if"):
            return self.if_stmt()
        if self.at("do"):
            return self.do_stmt()
        if self.at("print"):
            self.next()
            args = [self.expr()]
            while self.accept(","):
                args.append(self.expr())
            self.newline()
            return Print(args)
        if self.at("call"):
            self.next()
            name = self.ident()
            args = []
            if self.accept("("):
                if not self.at(")"):
                    args.append(self.arg())
                    while self.accept(","):
                        args.append(self.arg())
                self.expect(")")
            self.newline()
            return Call(name, args, t.line)
        if self.at("return"):
            self.next()
            val = None if self.tok.kind == "nl" else self.expr()
            self.newline()
            return Return(val)
        if self.at("exit"):
            self.next(); self.newline()
            return Exit()
        if self.at("cycle"):
            self.next(); self.newline()
            return Cycle()
        e = self.expr()
        if self.accept("="):
            if not isinstance(e, (Var, Apply)):
                self.error("cannot assign to this expression")
            v = self.expr()
            self.newline()
            return Assign(e, v, t.line)
        self.newline()
        return ExprStmt(e)

    def decl(self):
        line = self.tok.line
        typ = self.type_name()
        self.expect("::")
        decls = []
        while True:
            name = self.ident()
            size = None
            if self.accept("("):
                size = self.expr()
                self.expect(")")
            init = self.expr() if self.accept("=") else None
            decls.append(Decl(typ, name, size, init, line))
            if not self.accept(","):
                break
        self.newline()
        return decls if len(decls) > 1 else decls[0]

    def if_stmt(self):
        self.expect("if")
        branches, orelse = [], []
        cond = self.expr()
        self.expect("then")
        self.newline()
        branches.append((cond, self.block("if")))
        # block() stops at `else` (unconsumed) or after consuming `end`
        while self.at("else"):
            self.expect("else")
            if self.accept("if"):
                cond = self.expr()
                self.expect("then")
                self.newline()
                branches.append((cond, self.block("if")))
            else:
                self.newline()
                orelse = self.block("if")
        self.expect("if")
        self.newline()
        return If(branches, orelse)

    def do_stmt(self):
        line = self.expect("do").line
        if self.accept("while"):
            cond = self.expr()
            self.newline()
            body = self.block("do")
            self.expect("do")
            self.newline()
            return DoWhile(cond, body)
        var = self.ident()
        self.expect("=")
        lo = self.expr()
        self.expect(",")
        hi = self.expr()
        step = self.expr() if self.accept(",") else None
        self.newline()
        body = self.block("do")
        self.expect("do")
        self.newline()
        return Do(var, lo, hi, step, body, line)

    # -- expressions (lowest to highest precedence)
    def expr(self):
        return self.or_()

    def or_(self):
        e = self.and_()
        while self.accept("or"):
            e = Binary("or", e, self.and_())
        return e

    def and_(self):
        e = self.not_()
        while self.accept("and"):
            e = Binary("and", e, self.not_())
        return e

    def not_(self):
        if self.accept("not"):
            return Unary("not", self.not_())
        return self.compare()

    def compare(self):
        e = self.add()
        while self.at("==", "/=", "<", "<=", ">", ">="):
            op = self.next().text
            e = Binary(op, e, self.add())
        return e

    def add(self):
        e = self.mul()
        while self.at("+", "-"):
            op = self.next().text
            e = Binary(op, e, self.mul())
        return e

    def mul(self):
        e = self.unary()
        while self.at("*", "/"):
            op = self.next().text
            e = Binary(op, e, self.unary())
        return e

    def unary(self):
        if self.at("-", "+"):
            op = self.next().text
            return Unary(op, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self.accept("**"):
            return Binary("**", base, self.unary())  # right-associative
        return base

    def primary(self):
        t = self.tok
        if t.kind == "int":
            self.next(); return Num(int(t.text))
        if t.kind == "real":
            self.next(); return Num(float(t.text))
        if t.kind == "str":
            self.next(); return Str(t.text[1:-1])
        if self.accept("true"):
            return Bool(True)
        if self.accept("false"):
            return Bool(False)
        if self.accept("("):
            e = self.expr()
            self.expect(")")
            return e
        if self.accept("["):
            items = []
            if not self.at("]"):
                items.append(self.expr())
                while self.accept(","):
                    items.append(self.expr())
            self.expect("]")
            return ArrayLit(items)
        if t.kind == "ident":
            name = self.next().text
            if self.accept("("):
                args = []
                if not self.at(")"):
                    while True:
                        args.append(self.arg())
                        if not self.accept(","):
                            break
                self.expect(")")
                return Apply(name, args, t.line)
            return Var(name)
        self.error("expected an expression")

    def arg(self):
        lo = None if self.at(":") else self.expr()
        if self.accept(":"):
            hi = None if self.at(",", ")") else self.expr()
            return Slice(lo, hi)
        return lo


def parse(src):
    return Parser(src).parse()
