"""[중첩 반복문] 규칙."""

import ast

from .. import Match
from . import is_call_to, rule, walk_local


@rule
def same_loop_variable(tree: ast.Module):
    """바깥 for 와 안쪽 for 에 같은 반복 변수(i)를 쓴다. 둘 다 range(...) 를 돌 때만 본다
    (같은 반복자를 안쪽에서 이어서 읽는 코드는 일부러 같은 이름을 쓰기도 한다)."""
    for outer in ast.walk(tree):
        if not (isinstance(outer, ast.For) and isinstance(outer.target, ast.Name) and outer.target.id != "_"
                and is_call_to(outer.iter, "range")):
            continue
        for inner in walk_local(outer):
            if (inner is not outer and isinstance(inner, ast.For) and isinstance(inner.target, ast.Name)
                    and inner.target.id == outer.target.id and is_call_to(inner.iter, "range")):
                yield Match("nested.same_loop_var", inner.lineno, {"name": inner.target.id, "other_line": outer.lineno})
                break


@rule
def shared_rows(tree: ast.Module):
    """[[0] * 3] * 4 처럼 리스트를 * 로 늘려 2차원 리스트를 만든다. 모든 행이 같은 리스트가 된다."""
    for n in ast.walk(tree):
        if (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult)
                and isinstance(n.left, ast.List) and len(n.left.elts) == 1):
            row = n.left.elts[0]  # [[None]] * n 처럼 한 칸짜리는 행을 통째로 바꿔 끼우는 용도로도 쓴다
            if (isinstance(row, ast.List) and len(row.elts) >= 2) or (
                isinstance(row, ast.BinOp) and isinstance(row.op, ast.Mult) and isinstance(row.left, ast.List)
            ):
                yield Match("nested.shared_rows", n.lineno, {})
