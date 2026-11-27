"""Lexer and parser of `.hfx` source (grammar in docs/hdfx-lang.md section 2) -> a small AST."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .isa import VALUE_TYPES


class LangError(ValueError):
    def __init__(self, msg: str, line: int | None = None):
        super().__init__(f"line {line}: {msg}" if line else msg)
        self.line = line


# ---------------------------------------------------------------------- AST
@dataclass
class Node:
    line: int = field(default=0, kw_only=True)


@dataclass
class Num(Node):
    value: float | int
    is_int: bool


@dataclass
class Name(Node):
    name: str


@dataclass
class Str(Node):
    value: str


@dataclass
class Member(Node):
    obj: Node
    name: str


@dataclass
class Unary(Node):
    op: str
    e: Node


@dataclass
class Binary(Node):
    op: str
    a: Node
    b: Node


@dataclass
class Ternary(Node):
    c: Node
    a: Node
    b: Node


@dataclass
class Call(Node):
    f: Node
    args: list
    tmpl: str | None = None     # __call<type>


@dataclass
class Decl(Node):
    type: str
    name: str
    e: Node


@dataclass
class Assign(Node):
    target: Node
    op: str
    e: Node


@dataclass
class ExprStmt(Node):
    e: Node


@dataclass
class If(Node):
    c: Node
    then: list
    other: list


@dataclass
class Extern(Node):
    kind: str
    alias: str
    ext: str | None
    desc: list | None


@dataclass
class Pragma(Node):
    name: str
    args: list


def path_of(e: Node) -> str | None:
    """`a.b.c` for a chain of names, else None."""
    if isinstance(e, Name):
        return e.name
    if isinstance(e, Member):
        p = path_of(e.obj)
        return None if p is None else f"{p}.{e.name}"
    return None


# ---------------------------------------------------------------------- lexer
TOKEN = re.compile(r"""
    (?P<ws>[ \t\r]+) | (?P<nl>\n) | (?P<lc>//[^\n]*) | (?P<bc>/\*.*?\*/)
  | (?P<pragma>\#[A-Za-z_]+)
  | (?P<num>0[xX][0-9A-Fa-f]+ | (?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fF]?)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<str>"[^"\n]*")
  | (?P<op><<=|>>=|<<|>>|<=|>=|==|!=|&&|\|\||\+=|-=|\*=|/=|[-+*/%<>=!~&|^?:;,.(){}\[\]])
""", re.S | re.X)


@dataclass
class Tok:
    kind: str
    text: str
    line: int


def lex(src: str) -> list[Tok]:
    out, p, line = [], 0, 1
    while p < len(src):
        prev = out[-1] if out else None
        # a swizzle may start with a digit: `.111x`, `.xyz1` after a value
        if src[p] == "." and prev and (prev.kind == "id" or prev.text in (")", "]")):
            m = re.compile(r"\.([A-Za-z0-9_]+)").match(src, p)
            if m and (m.group(1)[0].isdigit() and re.fullmatch(r"[xyzwrgba01]+", m.group(1))):
                out += [Tok("op", ".", line), Tok("id", m.group(1), line)]
                p = m.end()
                continue
        m = TOKEN.match(src, p)
        if not m:
            raise LangError(f"unexpected character {src[p]!r}", line)
        k = m.lastgroup
        t = m.group()
        if k == "nl":
            line += 1
        elif k == "bc":
            line += t.count("\n")
        elif k not in ("ws", "lc"):
            out.append(Tok(k, t, line))
        p = m.end()
    out.append(Tok("eof", "", line))
    return out


# ---------------------------------------------------------------------- parser
BINOPS = [["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="], ["<", "<=", ">", ">="], ["<<", ">>"], ["+", "-"],
          ["*", "/", "%"]]
EXTERN_KINDS = set(VALUE_TYPES) | {"ctx", "curve1", "curve2", "curve3", "curve4", "shape", "vfield",
                                   "eventstream", "event", "none"}


class Parser:
    def __init__(self, src: str):
        self.t = lex(src)
        self.p = 0

    def peek(self, k=0) -> Tok:
        return self.t[min(self.p + k, len(self.t) - 1)]

    def next(self) -> Tok:
        tok = self.t[self.p]
        self.p += 1
        return tok

    def accept(self, text: str) -> bool:
        if self.peek().text == text and self.peek().kind in ("op", "id"):
            self.p += 1
            return True
        return False

    def expect(self, text: str) -> Tok:
        tok = self.next()
        if tok.text != text:
            raise LangError(f"expected {text!r}, found {tok.text or 'end of file'!r}", tok.line)
        return tok

    def ident(self) -> Tok:
        tok = self.next()
        if tok.kind != "id":
            raise LangError(f"expected a name, found {tok.text!r}", tok.line)
        return tok

    def dotted(self) -> str:
        s = self.ident().text
        while self.peek().text == "." and self.peek(1).kind == "id":
            self.p += 1
            s += "." + self.next().text
        return s

    # --- top level
    def script(self) -> list:
        out = []
        while self.peek().kind != "eof":
            out.append(self.item())
        return out

    def item(self):
        tok = self.peek()
        if tok.kind == "pragma":
            self.next()
            args = []
            while self.peek().line == tok.line and self.peek().kind != "eof":
                a = self.next()
                args.append(a.text[1:-1] if a.kind == "str" else a.text)
            return Pragma(tok.text[1:], args, line=tok.line)
        if tok.kind == "id" and tok.text == "extern":
            self.next()
            kind = self.ident().text
            if kind not in EXTERN_KINDS:
                raise LangError(f"unknown extern kind {kind!r}", tok.line)
            alias = self.dotted() if kind != "none" or self.peek().kind == "id" else ""
            ext = None
            if self.accept("="):
                s = self.next()
                if s.kind != "str":
                    raise LangError("extern: expected a \"name\"", s.line)
                ext = s.value if hasattr(s, "value") else s.text[1:-1]
            desc = None
            if self.accept("["):
                desc = []
                while not self.accept("]"):
                    desc.append(self.number_lit())
            self.expect(";")
            return Extern(kind, alias, ext, desc, line=tok.line)
        return self.stmt()

    def number_lit(self) -> int:
        neg = self.accept("-")
        tok = self.next()
        if tok.kind != "num":
            raise LangError("expected a number", tok.line)
        v = int(tok.text, 0)
        return -v if neg else v

    def block(self) -> list:
        if self.accept("{"):
            out = []
            while not self.accept("}"):
                if self.peek().kind == "eof":
                    raise LangError("unclosed {", self.peek().line)
                out.append(self.stmt())
            return out
        return [self.stmt()]

    def stmt(self):
        tok = self.peek()
        if tok.kind == "id" and tok.text == "if":
            self.next()
            self.expect("(")
            c = self.expr()
            self.expect(")")
            then = self.block()
            other = self.block() if self.accept("else") else []
            return If(c, then, other, line=tok.line)
        if tok.kind == "id" and tok.text in VALUE_TYPES and self.peek(1).kind == "id":
            self.next()
            name = self.ident().text
            self.expect("=")
            e = self.expr()
            self.expect(";")
            return Decl(tok.text, name, e, line=tok.line)
        e = self.expr()
        if self.peek().text in ("=", "+=", "-=", "*=", "/="):
            op = self.next().text
            v = self.expr()
            self.expect(";")
            if not isinstance(e, (Name, Member)):
                raise LangError("cannot assign to this", tok.line)
            return Assign(e, op, v, line=tok.line)
        self.expect(";")
        return ExprStmt(e, line=tok.line)

    # --- expressions
    def expr(self):
        c = self.binary(0)
        if self.accept("?"):
            a = self.expr()
            self.expect(":")
            b = self.expr()
            return Ternary(c, a, b, line=c.line)
        return c

    def binary(self, lvl: int):
        if lvl == len(BINOPS):
            return self.unary()
        a = self.binary(lvl + 1)
        while self.peek().kind == "op" and self.peek().text in BINOPS[lvl]:
            op = self.next().text
            a = Binary(op, a, self.binary(lvl + 1), line=a.line)
        return a

    def unary(self):
        tok = self.peek()
        if tok.kind == "op" and tok.text in ("-", "!", "~", "+"):
            self.next()
            e = self.unary()
            if tok.text == "+":
                return e
            if tok.text == "-" and isinstance(e, Num):
                return Num(-e.value, e.is_int, line=tok.line)
            return Unary(tok.text, e, line=tok.line)
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            if self.accept("."):
                e = Member(e, self.ident().text, line=e.line)
            elif self.peek().text == "(" and isinstance(e, (Name, Member)):
                self.next()
                e = Call(e, self.args(), line=e.line)
            else:
                return e

    def args(self) -> list:
        out = []
        if self.accept(")"):
            return out
        while True:
            out.append(self.expr())
            if self.accept(")"):
                return out
            self.expect(",")

    def primary(self):
        tok = self.next()
        if tok.kind == "num":
            t = tok.text
            if t.lower().startswith("0x"):
                return Num(int(t, 16), True, line=tok.line)
            is_int = not re.search(r"[.eEfF]", t)
            return Num(int(t) if is_int else float(t.rstrip("fF")), is_int, line=tok.line)
        if tok.kind == "str":
            return Str(tok.text[1:-1], line=tok.line)
        if tok.kind == "id":
            if tok.text == "__call":
                self.expect("<")
                tm = self.ident().text
                self.expect(">")
                self.expect("(")
                args = self.args()
                return Call(Name("__call", line=tok.line), args, tm, line=tok.line)
            return Name(tok.text, line=tok.line)
        if tok.text == "(":
            e = self.expr()
            self.expect(")")
            e.paren = True
            return e
        raise LangError(f"unexpected {tok.text or 'end of file'!r}", tok.line)


def parse(src: str) -> list:
    return Parser(src).script()
