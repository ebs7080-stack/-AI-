"""[조건문] 규칙. (input 글자와 숫자 비교는 var.input_not_converted 가 함께 찾는다.)"""

import ast

from .. import Match
from . import code_of, is_number_const, rule


def _tests(tree: ast.Module):
    """조건 자리에 쓴 식들 (if, elif, while, 조건식)."""
    for n in ast.walk(tree):
        if isinstance(n, (ast.If, ast.While, ast.IfExp)):
            yield n.test


@rule
def or_constant(tree: ast.Module):
    """x == 1 or 2 처럼 or 뒤에 비교 없이 값만 쓴다. 뒤쪽 값 혼자 참이라 조건이 늘 참이 된다."""
    for test in _tests(tree):
        for n in ast.walk(test):
            if (isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or)
                    and isinstance(n.values[0], ast.Compare) and isinstance(n.values[0].left, ast.Name)):
                const = next((v for v in n.values[1:] if isinstance(v, ast.Constant) and v.value), None)
                if const is not None:
                    yield Match("cond.or_constant", n.lineno, {"name": n.values[0].left.id, "expr": code_of(const)})


def _chain(node: ast.If) -> list[ast.If]:
    """if / elif / elif ... 를 차례대로 펼친다."""
    chain = [node]
    while len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
        node = node.orelse[0]
        chain.append(node)
    return chain


def _bound(test: ast.expr):
    """'이름 부등호 숫자' 꼴이면 (이름, 방향, 숫자, 같음 포함 여부). 방향은 위쪽(>)이 +1, 아래쪽(<)이 -1."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.left, ast.Name)
            and is_number_const(test.comparators[0])):
        return None
    op, value = test.ops[0], test.comparators[0].value
    if isinstance(op, (ast.Gt, ast.GtE)):
        return test.left.id, 1, value, isinstance(op, ast.GtE)
    if isinstance(op, (ast.Lt, ast.LtE)):
        return test.left.id, -1, value, isinstance(op, ast.LtE)
    return None


def _covers(wide, narrow) -> bool:
    """wide 조건이 참인 값이 narrow 조건이 참인 값을 모두 포함하는지 (그러면 narrow 쪽엔 닿지 못한다)."""
    name, direction, a, a_eq = wide
    name2, direction2, b, b_eq = narrow
    if name != name2 or direction != direction2:
        return False
    if direction * (b - a) > 0:  # 더 좁은 쪽으로 들어갔다 (>= 60 다음의 >= 90)
        return True
    return b == a and (a_eq or not b_eq)


@rule
def wide_condition_first(tree: ast.Module):
    """if s >= 60 다음에 elif s >= 90 처럼 넓은 조건을 먼저 검사해 뒤 조건에 닿지 못한다."""
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or id(node) in seen:
            continue
        earlier = []
        for branch in _chain(node):
            seen.add(id(branch))
            bound = _bound(branch.test)
            if bound is None:
                continue
            hit = next((line for line, b in earlier if _covers(b, bound)), None)
            if hit is not None:
                yield Match("cond.wide_condition_first", branch.lineno, {"name": bound[0], "other_line": hit})
                break
            earlier.append((branch.lineno, bound))
