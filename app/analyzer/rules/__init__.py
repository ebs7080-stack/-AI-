"""문법은 통과한 코드에서 논리 오류의 의심 지점을 찾는 규칙들 (docs/static-analysis.md 3단계).

규칙 하나는 트리를 받아 Match 를 내놓는 함수이고 @rule 로 등록한다. 분류별 모듈(var, cond, loop,
func, nested)에 있다. 규칙은 오탐보다 미탐을 택한다: 과제에 따라 맞을 수도 있는 모양은 잡지 않고,
확실하지 않으면 조건을 좁혀 그냥 지나친다. 코드를 실행하지 않고 트리만 읽는다.
"""

import ast
import builtins
import logging
from collections.abc import Callable, Iterable, Iterator

from .. import Match

Rule = Callable[[ast.Module], Iterable[Match]]
RULES: list[Rule] = []
BUILTIN_NAMES = frozenset(dir(builtins))
NUMBER_CALLS = frozenset({"int", "float", "len", "sum", "round", "abs"})

log = logging.getLogger(__name__)


def rule(fn: Rule) -> Rule:
    RULES.append(fn)
    return fn


def run(tree: ast.Module) -> list[Match]:
    """등록된 규칙을 모두 돌려 찾은 것을 모은다. 규칙 하나가 실패해도 나머지는 돈다."""
    found = []
    for fn in RULES:
        try:
            found.extend(fn(tree))
        except Exception:  # 규칙의 버그로 학생의 제출이 실패하면 안 된다.
            log.exception("논리 규칙 %s 가 실패했습니다", fn.__name__)
    return found


# ---------- 트리를 읽는 도우미 ----------

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
LOOPS = (ast.For, ast.AsyncFor, ast.While)


def walk_local(node: ast.AST, *, skip: tuple[type, ...] = SCOPES) -> Iterator[ast.AST]:
    """node 안을 훑되 skip 종류의 안쪽(기본은 함수·클래스 정의)으로는 내려가지 않는다."""
    yield node
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        yield child
        if not isinstance(child, skip):
            stack.extend(ast.iter_child_nodes(child))


def assigned_names(node: ast.AST) -> set[str]:
    """node 안에서 값이 대입되는 이름 (함수 정의 안쪽은 빼고)."""
    return {
        n.id for n in walk_local(node) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))
    }


def loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in walk_local(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def is_call_to(node: ast.AST, *names: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names


def blocks(tree: ast.AST) -> Iterator[list[ast.stmt]]:
    """코드 안의 모든 문장 묶음 (if 본문, else 쪽, 반복문 본문, 함수 본문 ...)."""
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
                yield stmts


def functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """맨 바깥에 정의한 평범한 함수들 (데코레이터·클래스 메서드는 빼서 단순하게 본다)."""
    return [s for s in tree.body if isinstance(s, ast.FunctionDef) and not s.decorator_list]


def exits_loop(loop: ast.AST) -> bool:
    """이 반복문을 빠져나갈 수 있는 문장이 있는지 (break, return, raise, exit())."""
    for n in walk_local(loop, skip=(*SCOPES, *LOOPS)):
        if n is not loop and isinstance(n, LOOPS):
            # 안쪽 반복문의 break 는 이 반복문을 끝내지 못하지만, return·raise 는 끝낸다.
            if any(isinstance(m, (ast.Return, ast.Raise)) or _is_exit_call(m) for m in walk_local(n)):
                return True
            continue
        if isinstance(n, (ast.Break, ast.Return, ast.Raise)) or _is_exit_call(n):
            return True
    return False


def _is_exit_call(n: ast.AST) -> bool:
    if is_call_to(n, "exit", "quit"):
        return True
    return isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "exit"


class Stores:
    """이름마다 어떤 값이 대입됐는지 모은다. 코드 전체를 한 스코프처럼 본다 (입문자 코드라 충분하다).

    'x = 값' 꼴의 대입만 값을 기억하고, 그 밖의 방법(for 변수, 매개변수, 여러 개 대입 ...)으로
    바뀐 이름은 값을 알 수 없는 것으로 표시한다. 그래서 '모든 대입이 숫자 상수다' 같은 판단이 안전하다.
    x += ... 는 값의 종류를 바꾸지 않으므로(숫자는 숫자, 글자는 글자로 남거나 그 줄에서 멈춘다) 모른다고 치지 않는다.
    """

    def __init__(self, tree: ast.Module):
        self.values: dict[str, list[ast.expr]] = {}
        self.unknown: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
                for t in node.targets:
                    self.values.setdefault(t.id, []).append(node.value)
            elif isinstance(node, ast.Assign):
                pass  # 아래의 Name(Store) 검사가 여러 개 대입의 이름을 unknown 으로 넣는다
            elif isinstance(node, ast.arg):
                self.unknown.add(node.arg)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                self.unknown.update(node.names)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                self.unknown.update((a.asname or a.name).split(".")[0] for a in node.names)
        simple = {
            id(t) for n in ast.walk(tree) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        } | {id(n.target) for n in ast.walk(tree) if isinstance(n, ast.AugAssign)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and id(node) not in simple:
                self.unknown.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.unknown.add(node.name)

    def only(self, name: str, test: Callable[[ast.expr], bool]) -> bool:
        """그 이름에 대입된 값이 모두 test 를 만족하는지 (한 번도 대입되지 않았거나 알 수 없으면 False)."""
        values = self.values.get(name)
        return bool(values) and name not in self.unknown and all(test(v) for v in values)


def root_name(n: ast.AST) -> str | None:
    """a.b[0].c 같은 식의 맨 앞 이름 (a). 이름으로 시작하지 않으면 None."""
    while isinstance(n, (ast.Attribute, ast.Subscript)):
        n = n.value
    return n.id if isinstance(n, ast.Name) else None


def inside_try(tree: ast.AST) -> set[int]:
    """try 의 본문 안에 있는 노드들의 id. 예외로 빠져나가는 반복문을 오탐하지 않으려고 쓴다."""
    return {id(n) for t in ast.walk(tree) if isinstance(t, ast.Try) for s in t.body for n in ast.walk(s)}


def is_number_const(n: ast.AST) -> bool:
    return isinstance(n, ast.Constant) and type(n.value) in (int, float)


def is_str_const(n: ast.AST) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, str)


def is_raw_input(n: ast.AST) -> bool:
    return is_call_to(n, "input")


ARITHMETIC = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


def is_number(n: ast.AST, stores: Stores, seen: frozenset[str] = frozenset()) -> bool:
    """확실히 숫자인 식: 숫자 상수, int()·len() 같은 호출, 숫자끼리의 계산, 숫자만 대입된 변수."""
    if is_number_const(n) or is_call_to(n, *NUMBER_CALLS):
        return True
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
        return is_number(n.operand, stores, seen)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ARITHMETIC):
        return is_number(n.left, stores, seen) and is_number(n.right, stores, seen)
    if isinstance(n, ast.Name):
        if n.id in seen:
            return True  # total = total + 1 처럼 자기 자신을 쓰는 대입은 나머지 쪽으로 판단한다
        return stores.only(n.id, lambda v: is_number(v, stores, seen | {n.id}))
    return False


def is_text(n: ast.AST) -> bool:
    return is_str_const(n) or isinstance(n, ast.JoinedStr) or is_call_to(n, "str", "input")


def starts_as_number(name: str, stores: Stores) -> bool:
    """total = 0 처럼 숫자로 시작했고 글자를 대입한 적은 없는 변수 (뒤에서 계산식으로 바뀌어도 된다)."""
    values = stores.values.get(name, [])
    return (name not in stores.unknown and any(is_number_const(v) for v in values)
            and not any(is_text(v) for v in values))


def kind(n: ast.AST, stores: Stores) -> type | None:
    """그 식이 확실히 숫자인지(int) 문자열인지(str). 모르면 None."""
    if is_number(n, stores):
        return int
    if is_str_const(n) or isinstance(n, ast.JoinedStr) or is_call_to(n, "str"):
        return str
    if isinstance(n, ast.Name) and stores.only(n.id, lambda v: is_str_const(v) or isinstance(v, ast.JoinedStr)):
        return str
    return None


def code_of(node: ast.AST) -> str:
    """학생 코드의 그 부분 (힌트에서 학생이 쓴 것을 가리킬 때만 쓴다. 고친 코드를 만들지 않는다)."""
    return ast.unparse(node)


# 분류별 규칙을 불러와 등록한다 (모듈을 읽는 것만으로 @rule 이 RULES 에 더해진다).
from . import cond, func, loop, nested, var  # noqa: E402,F401
