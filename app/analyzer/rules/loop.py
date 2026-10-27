"""[반복문] 규칙."""

import ast

from .. import Match
from . import (
    BUILTIN_NAMES, LOOPS, SCOPES, Stores, assigned_names, exits_loop, inside_try, is_call_to, loaded_names, root_name,
    rule, walk_local,
)

# 조건에 써도 값이 바뀌지 않는 내장 함수. 이것 말고 다른 함수를 부르는 조건은 알 수 없어서 건너뛴다.
PURE_CALLS = frozenset({"len", "abs", "int", "float", "str", "min", "max", "sum", "round"})
COUNT_NAMES = frozenset({"count", "cnt", "counter", "개수", "횟수"})
MUTATORS = frozenset({"remove", "pop", "insert", "append", "extend", "clear"})


@rule
def while_true_without_break(tree: ast.Module):
    """while True: 안에 반복을 끝내는 문장(break, return)이 하나도 없다."""
    in_try = inside_try(tree)
    for loop in ast.walk(tree):
        if not (isinstance(loop, ast.While) and isinstance(loop.test, ast.Constant) and loop.test.value):
            continue
        if id(loop) in in_try or any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await)) for n in walk_local(loop)):
            continue  # 예외로 빠져나가거나, 값을 하나씩 내주는(yield) 반복은 일부러 끝없이 쓴다
        if not exits_loop(loop):
            yield Match("loop.while_true_no_break", loop.lineno, {})


def _changed_in(body: list[ast.stmt]) -> set[str]:
    """반복문 본문에서 바뀔 수 있는 이름: 대입, +=, a.append(...) 같은 메서드 호출, a[i] = ..., del a[0]"""
    changed = set()
    for stmt in body:
        changed |= assigned_names(stmt)
        for n in walk_local(stmt):
            if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.ctx, (ast.Store, ast.Del)):
                changed.add(root_name(n))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                changed.add(root_name(n.func.value))
            if isinstance(n, (ast.Global, ast.Nonlocal)):
                changed.update(n.names)
    return changed


@rule
def while_condition_never_changes(tree: ast.Module):
    """while i < 5: 의 본문에서 조건에 쓴 i 를 한 번도 바꾸지 않는다."""
    user_functions = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.While) or isinstance(loop.test, ast.Constant):
            continue
        calls = [n for n in ast.walk(loop.test) if isinstance(n, ast.Call)]
        if any(not is_call_to(c, *PURE_CALLS) for c in calls):
            continue  # input() 이나 다른 함수를 부르는 조건은 부를 때마다 값이 바뀔 수 있다
        if any(isinstance(n, ast.Attribute) for n in ast.walk(loop.test)):
            continue  # t.error 같은 속성은 다른 곳(다른 스레드 등)에서 바뀔 수 있다
        used = loaded_names(loop.test) - BUILTIN_NAMES
        if not used or exits_loop(loop):
            continue
        body_calls = [n for n in walk_local(loop) if isinstance(n, ast.Call)]
        if {c.func.id for c in body_calls if isinstance(c.func, ast.Name)} & user_functions:
            continue  # 본문에서 부른 함수가 그 변수를 바꿀 수도 있다
        # print(i) 같은 내장 함수는 넘긴 값을 바꾸지 않지만, 그 밖의 함수(heappop(h) 등)는 바꿀 수 있다.
        passed = {
            a.id for c in body_calls if not is_call_to(c, *BUILTIN_NAMES)
            for a in c.args if isinstance(a, ast.Name)
        }
        if used & (_changed_in(loop.body) | passed):
            continue
        yield Match("loop.while_no_update", loop.lineno, {"name": ", ".join(sorted(used))})


@rule
def count_set_to_one(tree: ast.Module):
    """count = 0 으로 시작해 놓고 반복문 안에서 count += 1 대신 count = 1 이라고 쓴다."""
    stores = Stores(tree)
    starts_at_zero = {
        name for name, values in stores.values.items()
        if name.lower() in COUNT_NAMES and any(isinstance(v, ast.Constant) and v.value == 0 for v in values)
    }
    reported = set()
    for loop in ast.walk(tree):
        if not isinstance(loop, LOOPS):
            continue
        for n in walk_local(loop):
            if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                    and n.targets[0].id in starts_at_zero and isinstance(n.value, ast.Constant)
                    and n.value.value == 1 and n.lineno not in reported):
                name = n.targets[0].id
                if not any(isinstance(m, ast.AugAssign) and isinstance(m.target, ast.Name) and m.target.id == name
                           for m in walk_local(loop)):
                    reported.add(n.lineno)
                    yield Match("loop.count_set_to_one", n.lineno, {"name": name})


def _is_list_value(v: ast.expr) -> bool:
    if isinstance(v, (ast.List, ast.ListComp)):
        return True
    if is_call_to(v, "list", "sorted"):
        return True
    return isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "split"


@rule
def value_used_as_index(tree: ast.Module):
    """for i in scores: 에서 i 는 점수(값)인데 scores[i] 처럼 자리 번호로 쓴다."""
    stores = Stores(tree)
    for loop in ast.walk(tree):
        if not (isinstance(loop, ast.For) and isinstance(loop.target, ast.Name) and isinstance(loop.iter, ast.Name)):
            continue
        var, seq = loop.target.id, loop.iter.id
        if not stores.only(seq, _is_list_value):  # 사전(dict)을 돌 때의 d[k] 는 맞는 코드라서, 리스트일 때만
            continue
        for n in walk_local(loop):
            if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == seq
                    and isinstance(n.slice, ast.Name) and n.slice.id == var):
                yield Match("loop.value_as_index", n.lineno, {"name": var, "name2": seq, "other_line": loop.lineno})
                break


@rule
def list_changed_while_looping(tree: ast.Module):
    """for x in a: 를 도는 중에 a.remove(x) 처럼 그 리스트 자체를 바꾼다."""
    for loop in ast.walk(tree):
        if not (isinstance(loop, ast.For) and isinstance(loop.iter, ast.Name)):
            continue
        seq = loop.iter.id
        if any(seq in assigned_names(stmt) for stmt in loop.body):
            continue  # 도는 중에 그 이름에 새 리스트를 넣었다면 원래 리스트는 바뀌지 않는다
        for stmts in _blocks_in(loop.body):
            for i, stmt in enumerate(stmts):
                if not _mutates(stmt, seq):
                    continue
                if i + 1 < len(stmts) and isinstance(stmts[i + 1], (ast.Break, ast.Return)):
                    continue  # 바꾸자마자 반복을 끝내면 괜찮다
                yield Match("loop.modify_while_iterating", stmt.lineno, {"name": seq, "other_line": loop.lineno})
                return


def _blocks_in(body: list[ast.stmt]):
    """반복문 본문과 그 안의 if·else·안쪽 반복문 본문들 (함수 정의 안쪽은 빼고)."""
    yield body
    for stmt in body:
        if isinstance(stmt, SCOPES):
            continue
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(stmt, field, None)
            if isinstance(stmts, list) and stmts:
                yield from _blocks_in(stmts)
        for handler in getattr(stmt, "handlers", []):
            yield from _blocks_in(handler.body)


def _mutates(stmt: ast.stmt, seq: str) -> bool:
    if isinstance(stmt, ast.Expr):
        call = stmt.value
        return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id == seq
                and call.func.attr in MUTATORS)
    if isinstance(stmt, ast.Delete):
        return any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == seq
                   for t in stmt.targets)
    return False


@rule
def return_in_first_iteration(tree: ast.Module):
    """for 반복문 안에서 if 쪽과 else 쪽 모두 return 해서, 첫 번째 값만 보고 함수가 끝난다.

    조건 없이 바로 return 하는 반복문은 '첫 번째 것만 꺼내기'로 일부러 쓰기도 해서 잡지 않는다.
    """
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for loop in walk_local(func):
            if loop is func or not isinstance(loop, ast.For):
                continue
            for i, stmt in enumerate(loop.body):
                if any(isinstance(n, ast.Continue) for before in loop.body[:i] for n in ast.walk(before)):
                    break  # 앞에서 continue 로 건너뛸 수 있으면 일부러 그런 것일 수 있다
                if (isinstance(stmt, ast.If) and stmt.orelse
                        and any(isinstance(s, ast.Return) for s in stmt.body)
                        and any(isinstance(s, ast.Return) for s in stmt.orelse)):
                    yield Match("loop.return_in_loop", stmt.lineno, {"other_line": loop.lineno})
                    break
