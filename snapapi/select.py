from __future__ import annotations

import re

from snapapi.exceptions import SnapAPIError

_TOKEN_RE = re.compile(
    r"""
    \s*
    (?:
        (?P<lparen>\()
        |(?P<rparen>\))
        |(?P<quoted>"[^"]*"|'[^']*')
        |(?P<word>[A-Za-z0-9_][A-Za-z0-9_-]*)
    )
    """,
    re.VERBOSE,
)
_OPS = {"and": "AND", "or": "OR", "not": "NOT"}


def parse_bool_expr(expr):
    tokens = _tokenize(expr)
    if not tokens:
        return None
    parser = _ExprParser(tokens, expr)
    node = parser.parse_or()
    if parser.i < len(tokens):
        raise SnapAPIError(f"Unexpected extra expression text {tokens[parser.i][1]!r}")
    return node


def eval_keyword_expr(node, haystack):
    if node is None:
        return True
    text = str(haystack or "").lower()
    return _eval(node, lambda term: term.lower() in text)


def eval_tag_expr(node, tags):
    if node is None:
        return True
    wanted = {str(tag).lower() for tag in tags or []}
    return _eval(node, lambda term: term.lower() in wanted)


def _eval(node, match):
    kind = node[0]
    if kind == "term":
        return match(node[1])
    if kind == "not":
        return not _eval(node[1], match)
    if kind == "and":
        return _eval(node[1], match) and _eval(node[2], match)
    if kind == "or":
        return _eval(node[1], match) or _eval(node[2], match)
    raise SnapAPIError(f"Unknown expression node {kind}")


def _tokenize(expr):
    text = str(expr or "").strip()
    tokens = []
    i = 0
    while i < len(text):
        match = _TOKEN_RE.match(text, i)
        if not match:
            raise SnapAPIError(f"Invalid expression at {text[i:]!r}")
        i = match.end()
        if match.group("lparen"):
            tokens.append(("LPAREN", "("))
        elif match.group("rparen"):
            tokens.append(("RPAREN", ")"))
        elif match.group("quoted"):
            raw = match.group("quoted")
            tokens.append(("IDENT", raw[1:-1]))
        else:
            word = match.group("word")
            op = _OPS.get(word.lower())
            tokens.append((op, word) if op else ("IDENT", word))
    return tokens


class _ExprParser:
    def __init__(self, tokens, original):
        self.tokens = tokens
        self.original = original
        self.i = 0

    def parse_or(self):
        left = self.parse_and()
        while self._take("OR"):
            left = ("or", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_not()
        while True:
            if self._take("AND"):
                left = ("and", left, self.parse_not())
            elif self._peek() in ("IDENT", "NOT", "LPAREN"):
                left = ("and", left, self.parse_not())
            else:
                break
        return left

    def parse_not(self):
        if self._take("NOT"):
            return ("not", self.parse_not())
        if self._take("LPAREN"):
            node = self.parse_or()
            if not self._take("RPAREN"):
                raise SnapAPIError(f"Unbalanced parentheses in {self.original!r}")
            return node
        kind, value = self._expect("IDENT")
        return ("term", value)

    def _peek(self):
        if self.i >= len(self.tokens):
            return None
        return self.tokens[self.i][0]

    def _take(self, kind):
        if self._peek() != kind:
            return False
        self.i += 1
        return True

    def _expect(self, kind):
        if self._peek() != kind:
            raise SnapAPIError(f"Invalid expression {self.original!r}")
        token = self.tokens[self.i]
        self.i += 1
        return token
