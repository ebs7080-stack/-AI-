"""[변수] 규칙."""

import ast

from .. import Match
from . import (
    Stores, blocks, code_of, is_call_to, is_number_const, is_raw_input, is_str_const, kind, rule, walk_local, SCOPES,
)

# 학생이 변수 이름으로 자주 쓰는 내장 함수. 이 이름에 값을 넣은 뒤 함수로 부르면 멈춘다.
SHADOWED = frozenset({
    "sum", "max", "min", "list", "str", "int", "float", "input", "print", "len", "range",
    "abs", "round", "sorted", "dict", "set", "type",
})
# 숫자와 계산하면 멈추거나(+ - / // **) 엉뚱한 결과가 나오는(*) 연산. % 는 문자열 서식이라 뺀다.
MATH_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow)


def _top_level_stores(tree: ast.Module) -> list[ast.Name]:
    """함수 밖(맨 바깥의 if·for 안 포함)에서 값이 대입되는 이름."""
    return [
        n for stmt in tree.body if not isinstance(stmt, SCOPES) for n in walk_local(stmt)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    ]


@rule
def builtin_shadow_called(tree: ast.Module):
    """sum = 0 처럼 내장 함수 이름에 값을 넣은 뒤, 아래에서 sum(...) 으로 부른다."""
    first_store: dict[str, int] = {}
    for n in _top_level_stores(tree):
        if n.id in SHADOWED:
            first_store[n.id] = min(first_store.get(n.id, n.lineno), n.lineno)
    calls = sorted(
        (n.lineno, n.func.id) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in first_store
    )
    reported = set()
    for line, name in calls:
        if line > first_store[name] and name not in reported:
            reported.add(name)
            yield Match("var.builtin_shadowed", first_store[name], {"name": name, "other_line": line})


@rule
def str_plus_number(tree: ast.Module):
    """'나이: ' + 15 처럼 문자열과 숫자를 + 로 잇는다."""
    stores = Stores(tree)
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            if {kind(n.left, stores), kind(n.right, stores)} == {str, int}:
                yield Match("var.str_plus_number", n.lineno, {"expr": code_of(n)})


@rule
def input_not_converted(tree: ast.Module):
    """a = input() 으로 받은 글자를 숫자로 바꾸지 않고 숫자와 계산하거나 비교한다."""
    stores = Stores(tree)
    raw = {name for name in stores.values if stores.only(name, is_raw_input)}
    if not raw:
        return

    def is_raw(n):
        return isinstance(n, ast.Name) and n.id in raw

    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, MATH_OPS):
            for side, other in ((n.left, n.right), (n.right, n.left)):
                if is_raw(side) and kind(other, stores) is int:
                    yield Match("var.input_not_converted", n.lineno, {"name": side.id})
                    break
        elif isinstance(n, ast.AugAssign) and is_raw(n.value) and isinstance(n.target, ast.Name):
            # total = 0 ... total += a : total 이 숫자로 시작했는데 글자를 더한다.
            starts = stores.values.get(n.target.id, [])
            if starts and all(is_number_const(v) for v in starts):
                yield Match("var.input_not_converted", n.lineno, {"name": n.value.id})
        elif isinstance(n, ast.Compare):
            items = [n.left, *n.comparators]
            if any(kind(i, stores) is int for i in items):
                for i in items:
                    if is_raw(i):
                        yield Match("cond.input_str_compare", n.lineno, {"name": i.id})
                        break


@rule
def result_not_stored(tree: ast.Module):
    """x + 1 처럼 계산만 하고 버리거나, x == 5 처럼 값을 넣으려다 비교만 한다."""
    def simple(e):  # 이름과 숫자만으로 된 식 (path[:0] + sep 처럼 일부러 쓰는 표현은 빼려고)
        if isinstance(e, ast.BinOp):
            return simple(e.left) and simple(e.right)
        return isinstance(e, ast.Name) or is_number_const(e)

    for n in ast.walk(tree):
        if not isinstance(n, ast.Expr):
            continue
        v = n.value
        if isinstance(v, ast.BinOp) and simple(v):
            yield Match("var.result_not_stored", n.lineno, {"expr": code_of(v)})
        elif (isinstance(v, ast.Compare) and len(v.ops) == 1 and isinstance(v.ops[0], ast.Eq)
              and isinstance(v.left, ast.Name)):
            yield Match("var.eq_as_assign", n.lineno, {"name": v.left.id, "expr": code_of(v)})


@rule
def swap_without_temp(tree: ast.Module):
    """a = b 바로 다음 줄에 b = a 를 써서 두 변수를 바꾸려 한다."""

    def pair(s):
        if (isinstance(s, ast.Assign) and len(s.targets) == 1
                and isinstance(s.targets[0], ast.Name) and isinstance(s.value, ast.Name)):
            return s.targets[0].id, s.value.id
        return None

    for stmts in blocks(tree):
        for s1, s2 in zip(stmts, stmts[1:]):
            p, q = pair(s1), pair(s2)
            if p and q and p[0] != p[1] and p == (q[1], q[0]):
                yield Match("var.swap_without_temp", s1.lineno, {"name": p[0], "name2": p[1], "other_line": s2.lineno})


@rule
def quoted_variable(tree: ast.Module):
    """name 이라는 변수가 있는데 print("name") 처럼 이름을 따옴표로 감싸 출력한다."""
    first_store: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            first_store[n.id] = min(first_store.get(n.id, n.lineno), n.lineno)
    for n in ast.walk(tree):
        if is_call_to(n, "print") and len(n.args) == 1 and not n.keywords:
            arg = n.args[0]
            if is_str_const(arg) and arg.value in first_store and first_store[arg.value] < n.lineno:
                yield Match("var.quoted_variable", n.lineno, {"name": arg.value})
