"""[이름] 규칙 (docs/static-analysis.md 2단계): 실행하면 NameError 로 멈추는 곳을 찾는다.

코드는 위에서 아래로 실행되므로 문장 순서와 if/else·반복문 갈래를 따라가며 '여기서 이 이름이 확실히
만들어졌는가'를 센다. 반복문 안이나 try 안에서만 만든 이름은 '만들어졌을 수도 있음'으로 두고 잡지 않는다
(오탐보다 미탐). else 없는 if 에서만 만든 이름을 바로 뒤에서 쓰는 경우는 created_only_when_true 가 따로 본다.

함수 본문은 함수가 불릴 때 실행되므로, 함수 밖의 이름은 순서를 따지지 않고 '어딘가에서 만들었는가'만 본다.
클래스 본문과 lambda 안쪽은 보지 않는다.
"""

import ast
import builtins
import difflib
from dataclasses import dataclass, field

from .. import Match
from . import functions, rule

BUILTINS = frozenset(dir(builtins)) | {"__name__", "__file__", "__doc__", "exit", "quit"}
# 오타 후보로 견줄 내장 함수 (dir(builtins) 전체는 학생이 모르는 이름이 많아 엉뚱한 추천이 나온다).
COMMON_BUILTINS = frozenset({
    "print", "input", "int", "float", "str", "len", "range", "list", "dict", "set", "tuple", "sum", "max", "min",
    "abs", "round", "sorted", "type", "bool", "enumerate", "zip", "open", "reversed",
})
DYNAMIC_CALLS = frozenset({"exec", "eval", "globals", "locals", "vars", "setattr", "__import__"})
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)


def _walk_scope(node: ast.AST):
    """node 와 같은 스코프의 노드들. 함수·클래스·lambda·컴프리헨션 안쪽으로는 내려가지 않는다
    (node 가 정의문이면 그 노드만 돌려준다: 정의문은 이름만 만들고 본문은 다른 스코프다)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (*SCOPE_NODES, *COMPREHENSIONS)):
            stack.extend(ast.iter_child_nodes(n))


def bindings(stmts: list[ast.stmt]) -> dict[str, int]:
    """이 문장들이 같은 스코프에 만드는 이름과 처음 만든 줄."""
    out: dict[str, int] = {}

    def add(name: str, line: int):
        out[name] = min(out.get(name, line), line)

    for stmt in stmts:
        for n in _walk_scope(stmt):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                add(n.id, n.lineno)
            elif isinstance(n, (*DEFS, ast.ClassDef)):
                add(n.name, n.lineno)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    if a.name != "*":
                        add((a.asname or a.name).split(".")[0], n.lineno)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                add(n.name, n.lineno)
            elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
                add(n.name, n.lineno)
            elif isinstance(n, ast.MatchMapping) and n.rest:
                add(n.rest, n.lineno)
    return out


def _makes_hidden_names(n: ast.AST) -> bool:
    """from x import *, exec(...), globals()[...] = ..., Enum._convert_(..., __name__) 처럼
    코드에 보이지 않는 이름을 만드는 곳인지. 이런 코드가 있으면 이름 검사를 하지 않는다."""
    if isinstance(n, ast.ImportFrom):
        return any(a.name == "*" for a in n.names)
    if isinstance(n, ast.Call):
        if isinstance(n.func, ast.Name) and n.func.id in DYNAMIC_CALLS:
            return True
        return any(isinstance(a, ast.Name) and a.id == "__name__" for a in n.args)
    return isinstance(n, ast.Attribute) and n.attr == "modules" and isinstance(n.value, ast.Name) and n.value.id == "sys"


def _params(f) -> set[str]:
    a = f.args
    names = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    return names | {p.arg for p in (a.vararg, a.kwarg) if p}


def _declared(f) -> set[str]:
    """함수(와 그 안쪽 함수)에서 global·nonlocal 로 선언한 이름."""
    return {name for n in ast.walk(f) if isinstance(n, (ast.Global, ast.Nonlocal)) for name in n.names}


@dataclass
class Env:
    """지금 이 자리에서 확실히 만들어진 이름(definite)과 만들어졌을 수도 있는 이름(maybe)."""

    definite: set[str] = field(default_factory=set)
    maybe: set[str] = field(default_factory=set)

    def copy(self) -> "Env":
        return Env(set(self.definite), set(self.maybe))

    def define(self, names):
        for name in names:
            self.definite.add(name)
            self.maybe.discard(name)

    def might_have(self, names):
        new = set(names) - self.definite
        self.maybe |= new


def _merge(results: list[Env | None]) -> Env | None:
    """갈래가 다시 만나는 곳. 끝난 갈래(return, break ...)는 빼고, 모든 갈래에서 만든 이름만 확실하다."""
    alive = [e for e in results if e is not None]
    if not alive:
        return None
    definite = set.intersection(*(e.definite for e in alive))
    maybe = set.union(*(e.definite | e.maybe for e in alive)) - definite
    return Env(definite, maybe)


@dataclass
class Scope:
    kind: str  # "module" 또는 "function"
    name: str = ""
    locals: dict[str, int] = field(default_factory=dict)  # 함수 안에서 값을 넣는 이름과 그 줄 (global 선언은 빼고)
    outer: set[str] = field(default_factory=set)  # 감싼 함수의 이름들 (함수 안의 함수에서 쓸 수 있다)


class Checker:
    def __init__(self, tree: ast.Module):
        self.tree = tree
        self.found: list[Match] = []
        self.reported: set[tuple[str, str]] = set()
        self.module_bindings = bindings(tree.body)
        self.module_defs = {s.name: s.lineno for s in tree.body if isinstance(s, DEFS)}
        all_defs = [f for f in ast.walk(tree) if isinstance(f, DEFS)]
        # 함수 안에서 global 로 만든 이름: 언제 만들어지는지 알 수 없어 순서를 따지지 않는다.
        self.global_names = {name for f in all_defs for name in _declared(f)}
        # 함수 이름 -> (그 함수만의 이름들, 정의 줄). '함수 안 변수를 밖에서 쓰기'를 알아보는 데 쓴다.
        self.function_locals = {
            f.name: ((set(bindings(f.body)) | _params(f)) - _declared(f), f.lineno) for f in all_defs
        }
        # from x import *, exec(...), globals()[...] = ... 처럼 이름을 보이지 않게 만드는 코드는 따질 수 없다.
        self.dynamic_names = any(_makes_hidden_names(n) for n in ast.walk(tree))
        self.class_names = {name for c in ast.walk(tree) if isinstance(c, ast.ClassDef) for name in bindings(c.body)}
        self.calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.pending: list[tuple[ast.AST, Scope]] = []

    def run(self) -> list[Match]:
        if self.dynamic_names:
            return []
        self.block(self.tree.body, Env(), Scope("module"))
        while self.pending:
            self.function(*self.pending.pop(0))
        return self.found

    def function(self, f, outer: Scope):
        params = _params(f)
        local = {**bindings(f.body), **{p: f.lineno for p in params}}
        for name in _declared(f):
            local.pop(name, None)
        enclosing = (outer.outer | set(outer.locals)) if outer.kind == "function" else set()
        self.block(f.body, Env(definite=set(params)), Scope("function", f.name, local, enclosing))

    # ---------- 문장 ----------

    def block(self, stmts: list[ast.stmt], env: Env | None, scope: Scope) -> Env | None:
        for stmt in stmts:
            if env is None:
                return None  # return·break 뒤의 문장은 실행되지 않는다 (func.unreachable 이 알린다)
            env = self.stmt(stmt, env, scope)
        return env

    def stmt(self, s: ast.stmt, env: Env, scope: Scope) -> Env | None:
        if isinstance(s, DEFS):
            for e in (*s.decorator_list, *s.args.defaults, *filter(None, s.args.kw_defaults)):
                self.expr(e, env, scope)
            env.define([s.name])
            self.pending.append((s, scope))
            return env
        if isinstance(s, ast.ClassDef):
            for e in (*s.decorator_list, *s.bases):
                self.expr(e, env, scope)
            env.define([s.name])
            return env
        if isinstance(s, ast.Assign):
            self.expr(s.value, env, scope)
            for t in s.targets:
                self.target(t, env, scope)
            return env
        if isinstance(s, ast.AnnAssign):
            if s.value is not None:
                self.expr(s.value, env, scope)
                self.target(s.target, env, scope)
            return env
        if isinstance(s, ast.AugAssign):  # x += 1 은 x 를 먼저 읽는다
            if isinstance(s.target, ast.Name):
                self.load(s.target, env, scope)
            self.expr(s.value, env, scope)
            self.target(s.target, env, scope)
            return env
        if isinstance(s, (ast.For, ast.AsyncFor)):
            self.expr(s.iter, env, scope)
            body = self._loop_env(env, s.body)
            self.target(s.target, body, scope)
            self.block(s.body, body, scope)
            self.block(s.orelse, self._loop_env(env, s.orelse), scope)
            env.might_have(bindings([s]))  # 한 번도 안 돌 수 있다
            return env
        if isinstance(s, ast.While):
            self.expr(s.test, env, scope)
            self.block(s.body, self._loop_env(env, s.body), scope)
            self.block(s.orelse, self._loop_env(env, s.orelse), scope)
            env.might_have(bindings([s]))
            return env
        if isinstance(s, ast.If):
            self.expr(s.test, env, scope)
            return _merge([self.block(s.body, env.copy(), scope), self.block(s.orelse, env.copy(), scope)])
        if isinstance(s, (ast.With, ast.AsyncWith)):
            for item in s.items:
                self.expr(item.context_expr, env, scope)
                if item.optional_vars is not None:
                    self.target(item.optional_vars, env, scope)
            return self.block(s.body, env, scope)
        if isinstance(s, (ast.Try, ast.TryStar)):
            tried = bindings(s.body)
            body = self.block(s.body, env.copy(), scope)
            results = [self.block(s.orelse, body, scope) if body is not None else None]
            for h in s.handlers:
                if h.type is not None:
                    self.expr(h.type, env, scope)
                h_env = env.copy()
                h_env.might_have(tried)  # try 의 어디까지 실행됐는지 모른다
                if h.name:
                    h_env.define([h.name])
                results.append(self.block(h.body, h_env, scope))
            merged = _merge(results)
            if not s.finalbody:
                return merged
            final = merged.copy() if merged is not None else env.copy()
            final.might_have(tried)
            final = self.block(s.finalbody, final, scope)
            return final if merged is not None else None
        if isinstance(s, (ast.Return, ast.Raise)):
            for e in ast.iter_child_nodes(s):
                self.expr(e, env, scope)
            return None
        if isinstance(s, (ast.Break, ast.Continue)):
            return None
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            env.define(bindings([s]))
            return env
        if isinstance(s, ast.Delete):
            for t in s.targets:
                if isinstance(t, ast.Name):
                    self.load(t, env, scope)
                    env.definite.discard(t.id)
                else:
                    self.target(t, env, scope)
            return env
        if isinstance(s, ast.Match):
            self.expr(s.subject, env, scope)
            env.might_have(bindings([s]))  # 어느 case 가 맞을지 모른다
            return env
        for e in ast.iter_child_nodes(s):  # Expr, Assert 등: 들어 있는 식을 모두 본다
            if isinstance(e, ast.expr):
                self.expr(e, env, scope)
        return env

    def _loop_env(self, env: Env, body: list[ast.stmt]) -> Env:
        """반복문 본문: 두 번째부터는 앞 반복에서 만든 이름이 있으므로, 본문에서 만드는 이름은 '있을 수도 있음'."""
        inner = env.copy()
        inner.might_have(bindings(body))
        return inner

    # ---------- 식 ----------

    def target(self, t: ast.expr, env: Env, scope: Scope):
        """대입 대상: a[i] = ... 의 a·i 는 읽는 것이고, 이름 자체는 새로 만든다."""
        names = []
        for n in ast.walk(t):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                names.append(n.id)
            elif isinstance(n, (ast.Subscript, ast.Attribute)) and isinstance(n.ctx, (ast.Store, ast.Del)):
                self.expr(n.value, env, scope)
                if isinstance(n, ast.Subscript):
                    self.expr(n.slice, env, scope)
        env.define(names)

    def expr(self, e: ast.AST, env: Env, scope: Scope, bound: frozenset[str] = frozenset()):
        if isinstance(e, ast.Name):
            if isinstance(e.ctx, ast.Load) and e.id not in bound:
                self.load(e, env, scope)
        elif isinstance(e, ast.Lambda):
            for d in (*e.args.defaults, *filter(None, e.args.kw_defaults)):
                self.expr(d, env, scope, bound)  # 본문은 불릴 때 실행된다
        elif isinstance(e, ast.NamedExpr):
            self.expr(e.value, env, scope, bound)
            env.define([e.target.id])
        elif isinstance(e, COMPREHENSIONS):
            self.expr(e.generators[0].iter, env, scope, bound)  # 첫 번째 for 의 대상만 바깥에서 계산된다
            inner = set(bound)
            for i, g in enumerate(e.generators):
                if i:
                    self.expr(g.iter, env, scope, frozenset(inner))
                inner |= {n.id for n in ast.walk(g.target) if isinstance(n, ast.Name)}
                for cond in g.ifs:
                    self.expr(cond, env, scope, frozenset(inner))
            for part in ([e.key, e.value] if isinstance(e, ast.DictComp) else [e.elt]):
                self.expr(part, env, scope, frozenset(inner))
        else:
            for child in ast.iter_child_nodes(e):
                if isinstance(child, (ast.expr, ast.keyword, ast.FormattedValue)):
                    self.expr(child, env, scope, bound)

    # ---------- 이름 하나 ----------

    def load(self, n: ast.Name, env: Env, scope: Scope):
        name = n.id
        if name in env.definite or name in BUILTINS or name in self.global_names:
            return
        if name in env.maybe or name in self.class_names:
            return  # 만들어졌을 수도 있다 / 클래스 안에서 만든 이름(@global_enum 처럼 바깥에 풀리기도 한다)
        if scope.kind == "function":
            if name in scope.locals:
                if name in self.module_bindings:
                    return  # 바깥 변수를 global 없이 바꾸는 경우: func.global_without_decl 이 알린다
                self.report("var.used_before_assign", n.lineno, name, {"other_line": scope.locals[name]})
                return
            if name in scope.outer or name in self.module_bindings:
                return  # 함수가 불릴 때 바깥에 있으면 된다 (부르는 순서는 따지지 않는다)
        self.missing(n, scope)

    def missing(self, n: ast.Name, scope: Scope):
        name, line = n.id, n.lineno
        is_call = id(n) in self.calls
        if scope.kind == "module" and name in self.module_bindings:  # 아래에서 만든다
            if is_call and name in self.module_defs:
                self.report("func.called_before_def", line, name, {"other_line": self.module_defs[name]})
            else:
                self.report("var.used_before_assign", line, name, {"other_line": self.module_bindings[name]})
            return
        owner = next((f for f, (names, _) in self.function_locals.items() if name in names and f != scope.name), None)
        if owner is not None:
            self.report("func.local_used_outside", line, name,
                        {"name2": owner, "other_line": self.function_locals[owner][1]})
            return
        similar = self.similar(name, scope)
        if similar:
            self.report("func.call_typo" if is_call else "var.name_typo", line, name, {"name2": similar})
        else:
            self.report("func.undefined_function" if is_call else "var.undefined_name", line, name, {})

    def similar(self, name: str, scope: Scope) -> str | None:
        """오타로 보이는 비슷한 이름. 대소문자만 다르면 그것을, 아니면 철자가 아주 비슷한 것을 고른다."""
        known = set(self.module_bindings) | set(scope.locals) | scope.outer | COMMON_BUILTINS | self.global_names
        same_letters = sorted(k for k in known if k.lower() == name.lower() and k != name)
        if same_letters:
            return same_letters[0]
        close = difflib.get_close_matches(name, sorted(known), n=1, cutoff=0.75)
        return close[0] if close else None

    def report(self, case_id: str, line: int, name: str, values: dict):
        if (case_id, name) in self.reported:
            return  # 같은 이름은 처음 한 번만 알린다
        self.reported.add((case_id, name))
        self.found.append(Match(case_id, line, {"name": name, **values}))


@rule
def names_that_do_not_exist(tree: ast.Module):
    """실행하면 NameError 로 멈추는 이름: 만들기 전에 쓰기, 오타, 없는 함수, 함수 안 변수를 밖에서 쓰기."""
    return Checker(tree).run()


@rule
def parameter_ignored(tree: ast.Module):
    """def square(x): return n * n 처럼 매개변수를 하나도 쓰지 않고 함수 밖의 변수를 쓴다.

    매개변수 일부만 안 쓰는 함수나, 바깥의 상수(MAX)·내부용(_cfg) 이름을 쓰는 함수는 일부러 그렇게
    쓰기도 해서 잡지 않는다.
    """
    plain = [s for s in tree.body if not isinstance(s, (*DEFS, ast.ClassDef, ast.Import, ast.ImportFrom))]
    variables = {  # 함수 밖에서 값을 넣은 평범한 변수 (함수·모듈·상수·내부용 이름은 빼고)
        name for name in bindings(plain) if not name.startswith("_") and not name.isupper()
    }
    for f in functions(tree):
        params = [p for p in _params(f) if p not in ("self", "_")]
        local = set(bindings(f.body)) | set(params)
        loads = sorted(
            (n for s in f.body for n in _walk_scope(s) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)),
            key=lambda n: (n.lineno, n.col_offset),
        )
        unused = sorted(p for p in params if p not in {n.id for n in loads})
        outer = [n for n in loads if n.id in variables and n.id not in local]
        if params and len(unused) == len(params) and outer:
            yield Match("func.ignores_param", outer[0].lineno,
                        {"name": unused[0], "name2": outer[0].id, "other_line": f.lineno})


def _scope_blocks(body: list[ast.stmt]):
    """한 스코프(모듈이나 함수 본문) 안의 문장 묶음들. 안쪽 함수·클래스 정의 안으로는 내려가지 않고,
    반복문 안으로도 내려가지 않는다 ('첫 번째 반복에서만 만든다'처럼 반복 횟수와 묶인 조건이 많다)."""
    yield body
    for stmt in body:
        if isinstance(stmt, (*DEFS, ast.ClassDef, ast.For, ast.AsyncFor, ast.While)):
            continue
        for name in ("body", "orelse", "finalbody"):
            inner = getattr(stmt, name, None)
            if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                yield from _scope_blocks(inner)
        for h in getattr(stmt, "handlers", []):
            yield from _scope_blocks(h.body)


def _direct_loads(stmt: ast.stmt) -> list[ast.Name]:
    """조건 없이 읽는 이름들 (조건식·and/or·lambda·컴프리헨션 안쪽은 뺀다: 같은 조건으로 막아 둔 경우가 있다)."""
    out, stack = [], list(ast.iter_child_nodes(stmt))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.IfExp, ast.BoolOp, ast.Lambda, *COMPREHENSIONS)):
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


SIMPLE = (ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Return)
ENDS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


@rule
def created_only_when_true(tree: ast.Module):
    """if score >= 60: result = "합격" 처럼 else 없는 if 에서만 변수를 만들고, 바로 뒤에서 그 변수를 쓴다.

    조건이 거짓이면 변수가 없어 멈춘다. 뒤에서 쓰는 곳이 다른 조건 안에 있으면 같은 조건으로 막아 둔
    코드일 수 있어서, if 와 같은 높이의 단순한 문장(print, 대입, return)에서 바로 쓸 때만 본다.
    """
    if any(_makes_hidden_names(n) for n in ast.walk(tree)):
        return
    scopes = [(tree.body, {})] + [
        # global 로 선언한 이름은 함수 밖에 이미 있는 변수라서, 미리 만든 것으로 친다.
        (f.body, {name: f.lineno for name in _params(f) | _declared(f)}) for f in ast.walk(tree) if isinstance(f, DEFS)
    ]
    for body, params in scopes:
        first_made = {**bindings(body), **params}
        for stmts in _scope_blocks(body):
            for i, s in enumerate(stmts):
                if not isinstance(s, ast.If) or s.orelse or any(isinstance(b, ENDS) for b in s.body):
                    continue
                new = {  # if 본문에서 처음 만드는 이름 (그 전에 한 번도 만든 적 없는 것)
                    t.id for b in s.body if isinstance(b, ast.Assign) for t in b.targets
                    if isinstance(t, ast.Name) and first_made.get(t.id) == t.lineno
                }
                for after in stmts[i + 1:]:
                    if not new:
                        break
                    if isinstance(after, SIMPLE):
                        hit = next((n for n in _direct_loads(after) if n.id in new), None)
                        if hit is not None:
                            yield Match("var.conditional_assign", hit.lineno, {"name": hit.id, "other_line": s.lineno})
                            new.discard(hit.id)
                    new -= set(bindings([after]))
