"""[함수] 규칙. 맨 바깥에 정의한 평범한 함수만 본다 (functions())."""

import ast

from .. import Match
from . import LOOPS, SCOPES, blocks, functions, is_call_to, is_number_const, rule, walk_local

# 값을 계산하기만 하는 내장 함수. 이것만 부르는 함수는 결과를 받지 않으면 아무 일도 하지 않은 것이다.
PURE_CALLS = frozenset({
    "len", "abs", "int", "float", "str", "min", "max", "sum", "round", "range", "sorted", "list", "bool",
})


def _stable(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """한 번만 정의되고 다른 값으로 덮어쓰이지 않은 함수. 호출을 그 정의와 짝지어도 안전하다."""
    defs: dict[str, list[ast.FunctionDef]] = {}
    for f in functions(tree):
        defs.setdefault(f.name, []).append(f)
    rebound = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
    all_defs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return {
        name: fs[0] for name, fs in defs.items()
        if len(fs) == 1 and name not in rebound and all_defs.count(name) == 1
    }


def _calls(tree: ast.Module, funcs: dict[str, ast.FunctionDef]):
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs:
            yield n, funcs[n.func.id]


def _value_returns(func: ast.FunctionDef) -> list[ast.Return]:
    return [n for n in walk_local(func) if isinstance(n, ast.Return) and n.value is not None]


def _is_generator(func: ast.FunctionDef) -> bool:
    return any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await)) for n in walk_local(func))


@rule
def wrong_argument_count(tree: ast.Module):
    """def area(w, h): 를 area(3) 처럼 필요한 개수와 다르게 부른다."""
    for call, func in _calls(tree, _stable(tree)):
        a = func.args
        if call.keywords or any(isinstance(arg, ast.Starred) for arg in call.args):
            continue
        if a.vararg or a.kwarg or a.kwonlyargs:
            continue
        positional = len(a.posonlyargs) + len(a.args)
        required = positional - len(a.defaults)
        given = len(call.args)
        if required <= given <= positional:
            continue
        expected = f"{required}개" if required == positional else f"{required}~{positional}개"
        yield Match("func.wrong_arg_count", call.lineno,
                    {"name": func.name, "count": f"{given}개", "expected": expected, "other_line": func.lineno})


@rule
def unpack_count_mismatch(tree: ast.Module):
    """return a, b 로 두 개를 돌려주는 함수를 x, y, z = f() 처럼 세 개로 받는다."""
    funcs = _stable(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple)):
            continue
        target = node.targets[0]
        if any(isinstance(e, ast.Starred) for e in target.elts):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in funcs):
            continue
        func = funcs[call.func.id]
        returns = [r for r in walk_local(func) if isinstance(r, ast.Return)]
        sizes = {len(r.value.elts) if isinstance(r.value, ast.Tuple) else None for r in returns}
        if len(sizes) != 1 or None in sizes or _is_generator(func):
            continue  # 돌려주는 개수가 한 가지로 정해진 함수만 본다
        (size,) = sizes
        if size != len(target.elts):
            yield Match("func.unpack_mismatch", node.lineno, {
                "name": func.name, "expected": f"{size}개", "count": f"{len(target.elts)}개",
                "other_line": returns[0].lineno,
            })


@rule
def global_without_declaration(tree: ast.Module):
    """함수 밖에서 만든 count 를 함수 안에서 global 없이 count += 1 로 바꾼다."""
    module_names = set()
    for stmt in tree.body:
        if not isinstance(stmt, SCOPES):
            module_names |= {
                n.id for n in walk_local(stmt) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
            }
    for func in functions(tree):
        declared = {name for n in walk_local(func) if isinstance(n, (ast.Global, ast.Nonlocal)) for name in n.names}
        params = {a.arg for a in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs)}
        first_store: dict[str, int] = {}  # 함수 안에서 그 이름에 처음 값을 넣은 줄
        for n in walk_local(func):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                first_store[n.id] = min(first_store.get(n.id, n.lineno), n.lineno)
        for n in walk_local(func):
            name, line = None, None
            if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                name, line = n.target.id, n.lineno
            elif (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                  and n.targets[0].id in {m.id for m in ast.walk(n.value) if isinstance(m, ast.Name)}):
                name, line = n.targets[0].id, n.lineno  # count = count + 1
            if name is None or name not in module_names or name in declared or name in params:
                continue
            if first_store.get(name, line) < line:
                continue  # 함수 안에서 먼저 만든 지역 변수다
            yield Match("func.global_without_decl", line, {"name": name, "other_line": func.lineno})
            break


@rule
def recursion_without_base_case(tree: ast.Module):
    """자기 자신을 부르는 함수에 멈출 조건(if)이 하나도 없다."""
    for func in _stable(tree).values():
        nodes = list(walk_local(func))
        if any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in nodes):
            continue
        self_calls = [n for n in nodes if is_call_to(n, func.name)]
        branches = (ast.If, ast.IfExp, ast.BoolOp, ast.Try, ast.Match, *LOOPS)
        if self_calls and not any(isinstance(n, branches) for n in nodes):
            yield Match("func.recursion_no_base", func.lineno, {"name": func.name, "other_line": self_calls[0].lineno})


@rule
def code_after_return(tree: ast.Module):
    """return 다음 줄에 쓴 코드는 실행되지 않는다."""
    for stmts in blocks(tree):
        for stmt, after in zip(stmts, stmts[1:]):
            if isinstance(stmt, ast.Return):
                yield Match("func.unreachable", after.lineno, {"other_line": stmt.lineno})
                break


def _always_returns(stmts: list[ast.stmt]) -> bool:
    """이 문장들을 지나면 어느 길로 가든 return(또는 raise)을 만나는지."""
    for stmt in stmts:
        if isinstance(stmt, (ast.Return, ast.Raise)):
            return True
        if isinstance(stmt, ast.If) and stmt.orelse and _always_returns(stmt.body) and _always_returns(stmt.orelse):
            return True
        if isinstance(stmt, ast.While) and isinstance(stmt.test, ast.Constant) and stmt.test.value:
            if not any(isinstance(n, ast.Break) for n in walk_local(stmt, skip=(*SCOPES, *LOOPS)) if n is not stmt):
                return True  # 끝나지 않는 반복은 return 으로만 빠져나간다
        if isinstance(stmt, (ast.Try, ast.Match, ast.With)):
            return True  # 따지기 어려운 문장은 돌려준다고 보고 넘어간다 (오탐을 피한다)
    return False


@rule
def missing_return_path(tree: ast.Module):
    """함수 끝의 if / elif 가 갈래마다 값을 돌려주는데 else 가 없어, 어느 조건에도 맞지 않으면 None 이 된다.

    맞지 않으면 None 을 돌려주는 함수는 일부러 그렇게 쓰기도 한다. 그래서 점수로 등급을 나누듯
    변수 하나를 '이름 부등호 숫자' 로 구간을 나누는 갈래 둘 이상의 if / elif 사슬만 본다.
    """
    for func in functions(tree):
        last = func.body[-1]
        if not isinstance(last, ast.If) or _is_generator(func):
            continue
        chain = [last]
        while len(chain[-1].orelse) == 1 and isinstance(chain[-1].orelse[0], ast.If):
            chain.append(chain[-1].orelse[0])
        if len(chain) < 2 or chain[-1].orelse:
            continue
        compared = {_ranged_name(b.test) for b in chain}
        if len(compared) != 1 or None in compared:
            continue
        if all(_always_returns(b.body) and any(isinstance(s, ast.Return) and s.value is not None for s in b.body)
               for b in chain):
            yield Match("func.missing_return", last.lineno, {"name": func.name, "other_line": func.lineno})


def _ranged_name(test: ast.expr) -> str | None:
    """'s >= 90', '80 <= s < 90' 처럼 이름 하나를 숫자와 크기 비교하는 조건이면 그 이름."""
    if not (isinstance(test, ast.Compare) and all(isinstance(op, (ast.Gt, ast.GtE, ast.Lt, ast.LtE)) for op in test.ops)):
        return None
    items = [test.left, *test.comparators]
    names = {i.id for i in items if isinstance(i, ast.Name)}
    if len(names) == 1 and all(isinstance(i, ast.Name) or is_number_const(i) for i in items):
        return names.pop()
    return None


def _only_computes(func: ast.FunctionDef) -> bool:
    """화면 출력·입력, 리스트 바꾸기, global 없이 값만 계산하는 함수인지."""
    for n in walk_local(func):
        if isinstance(n, ast.Call) and not is_call_to(n, *PURE_CALLS):
            return False
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            return False
        if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.ctx, (ast.Store, ast.Del)):
            return False
    return True


@rule
def return_value_ignored(tree: ast.Module):
    """값을 계산해 돌려주기만 하는 함수를 부르고, 그 결과를 받지 않는다."""
    funcs = _stable(tree)
    for n in ast.walk(tree):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name):
            func = funcs.get(n.value.func.id)
            if func and _value_returns(func) and not _is_generator(func) and _only_computes(func):
                yield Match("func.return_ignored", n.lineno, {"name": func.name, "other_line": func.lineno})


@rule
def none_result_used(tree: ast.Module):
    """return 이 없는 함수의 결과(None)를 변수에 넣거나 계산·출력에 쓴다."""
    funcs = {
        name: f for name, f in _stable(tree).items() if not _value_returns(f) and not _is_generator(f)
    }
    if not funcs:
        return
    users = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            users.append((n, [n.value]))
        elif isinstance(n, ast.BinOp):
            users.append((n, [n.left, n.right]))
        elif isinstance(n, ast.Compare):
            users.append((n, [n.left, *n.comparators]))
        elif is_call_to(n, "print"):
            users.append((n, n.args))
    reported = set()
    for user, values in users:
        for v in values:
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in funcs:
                func = funcs[v.func.id]
                if (func.name, v.lineno) in reported:
                    continue
                reported.add((func.name, v.lineno))
                prints = any(is_call_to(m, "print") for m in walk_local(func))
                case_id = "func.print_not_return" if prints else "func.none_used"
                yield Match(case_id, v.lineno, {"name": func.name, "other_line": func.lineno})


@rule
def call_without_parentheses(tree: ast.Module):
    """hello() 대신 hello 처럼 괄호 없이 함수 이름만 쓴 줄."""
    names = {f.name for f in functions(tree)} | {"print", "input"}
    for n in ast.walk(tree):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Name) and n.value.id in names:
            yield Match("func.missing_call_parens", n.lineno, {"name": n.value.id})


@rule
def defined_twice(tree: ast.Module):
    """같은 이름의 함수를 두 번 정의해서 앞의 것이 사라진다."""
    seen: dict[str, int] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.name in seen:
                yield Match("func.defined_twice", stmt.lineno, {"name": stmt.name, "other_line": seen[stmt.name]})
            seen.setdefault(stmt.name, stmt.lineno)


@rule
def function_name_overwritten(tree: ast.Module):
    """def total(): 을 만든 뒤 total = 0 처럼 같은 이름에 값을 넣고, 그 아래에서 total() 을 부른다."""
    defs = {f.name: f.lineno for f in functions(tree)}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for t in stmt.targets:
            if isinstance(t, ast.Name) and t.id in defs and defs[t.id] < stmt.lineno:
                later = [c.lineno for c in ast.walk(tree) if is_call_to(c, t.id) and c.lineno > stmt.lineno]
                if later:
                    yield Match("func.name_overwritten", stmt.lineno, {"name": t.id, "other_line": later[0]})
