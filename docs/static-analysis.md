# 정적 분석과 단계별 힌트 설계 (FR-1.2, FR-1.3)

> 상태 (2026-10-27): 1단계(문법), 2단계(이름: NameError 로 멈추는 9개 case, `rules/names.py`), 3단계(AST 규칙 중 오탐이 거의 없는 30개 case)를 구현했다. 4단계(실행)는 아직 없다. 한 번에 보여 주는 개수는 1개로 정했다(8번). 구현은 `app/analyzer/syntax.py`, `app/analyzer/rules/`, 목록과 문구는 `app/analyzer/cases.toml`.
> 근거 자료: `오류case.txt`(80여 개), `docs/requirements.md`.

웹 계층(스레드, 단계별 힌트 공개, 교사 대시보드)은 `analyze(code) -> list[Finding]`만 본다. 이 문서의 내용은 모두 이 함수 안쪽을 채우는 일이며, API·DB·화면은 바꾸지 않는다.

## 1. 전체 흐름

```
학생 제출 → analyze(code)
   ① 문법 검사 (ast.parse)        ── 실패하면 문법 case 1개만 반환하고 끝
   ② 이름·스코프 검사 (symtable/pyflakes 방식)
   ③ AST 패턴 규칙 (case별 detector)
   ④ (선택) 실행 검사: 별도 프로세스 + 타임아웃, 과제 테스트케이스 필요
   ⑤ 우선순위 정렬 → 상위 1~2개만 Finding으로 반환
→ findings.steps_json에 [관찰, 의심 지점, 힌트] 저장, revealed_steps=0
→ 학생(또는 짝이 된 도우미와 함께)이 한 단계씩 연다
→ 고쳐서 같은 thread_id로 다시 제출 → 다시 분석 → 사라진 case / 새 case 비교
```

원칙:

- **한 번에 1~2개만 보여 준다.** 문법 오류가 있을 때 논리 오류까지 쏟아내면 초보자는 무엇부터 고칠지 모른다.
- 우선순위: 문법 > 실행하면 멈추는 오류(NameError, TypeError 등) > 무한 반복 > 결과가 틀리는 논리 오류 > 스타일.
- 결과는 "확정"이 아니라 "의심 지점"이다. **오탐보다 미탐**을 택한다.
- 정답 코드(고친 코드)는 어느 단계에도 넣지 않는다 (FR-1.4).
- 줄 번호는 "의심 지점" 단계에서 처음 나오게 한다. 공개 전 힌트 본문·`case_id`·줄 번호를 응답에 넣지 않는 기존 규칙(`tests/test_api.py`)을 지킨다.
- 학생 코드는 웹 서버 프로세스에서 `exec`하지 않는다. 실행 검사는 별도 프로세스 + 타임아웃으로 격리한다.
- LLM은 마지막 보조로만 쓴다.

## 2. 제출 한 건의 처리 순서

아래 예시 코드 하나가 각 단계를 어떻게 지나가는지 따라간다. 처리는 `app/api.py`의 `create_submission`에서 시작한다.

```python
nums = input("숫자들: ").split()
m = 0
for n in nums:
    if n > m:
        m = n
print(m)
```

```
제출 → [0] 제출해도 되나? → [1] 문법 ──실패──→ 문법 case 1개
                              │통과
                              ▼
                         [2] 이름·스코프 → [3] AST 규칙 → [4] 실행(선택)
                              └──────── 찾은 것 전부 모으기 ────────┘
                                              ▼
                              [5] 우선순위로 1~2개 선택
                                              ▼
                  [6] 힌트 채워 저장 (공개 0단계) → [7] 한 단계씩 열기 → [8] 다시 제출
```

새로 만들 부분은 1~5단계와 6단계의 힌트 채우기이며 모두 `analyze()` 안에서 끝난다. 나머지는 이미 구현되어 있다.

### 0단계: 분석 전 확인 (구현됨)

코드를 보기 전에 제출해도 되는 상황인지 확인한다.

1. 로그인한 학생인지 (세션 쿠키)
2. 친구를 도와주는 중이면 거절 (409)
3. `thread_id`가 내 것이고 아직 열려 있는지
4. 바로 전 제출과 똑같은 코드면 거절 (422)

> 주의: 지금 코드는 `analysis.analyze()`를 2~4번 확인보다 **먼저** 호출한다. 실행 검사처럼 느린 단계가 붙으면 거절될 제출까지 분석하게 되므로, 분석기를 붙일 때 호출 위치를 확인 뒤로 옮긴다.

### 1단계: 문법 검사 (`ast.parse`)

파이썬이 이 코드를 읽을 수 있는지 본다. 실패하면 오류 메시지와 줄로 case를 정하고(예: `cond.missing_colon`) **그 case 하나만 반환하고 끝낸다.** 문법이 깨진 코드는 뒤 단계에서 분석할 수 없다. 예시 코드는 통과한다.

#### 파이썬이 알려주는 정보 (Python 3.12에서 확인, 2026-09-28)

`SyntaxError`에는 오류 종류, 메시지(`msg`), 줄(`lineno`), 칸(`offset`), 그 줄의 원문(`text`)이 들어 있다. 코드를 실행하지 않고 읽기만 하므로 안전하다.

| 학생의 실수 | 파이썬 메시지 | 줄 |
|---|---|---|
| `if score >= 60` (콜론 없음) | `expected ':'` | 정확 |
| `for i in range(3)` (콜론 없음) | `expected ':'` | 정확 |
| `def f(a)` (콜론 없음) | `expected ':'` | 정확 |
| `def f:` (괄호 없음) | `expected '('` | 정확 |
| if 본문 들여쓰기 안 함 | `IndentationError: expected an indented block after 'if' statement on line 1` | 정확 |
| if/else 들여쓰기 어긋남 | `IndentationError: unindent does not match any outer indentation level` | 정확 |
| 안쪽 for를 같은 들여쓰기에 씀 | `IndentationError: expected an indented block after 'for' statement on line 1` | 정확 |
| `if x = 1:` | `invalid syntax. Maybe you meant '==' or ':=' instead of '='?` | 정확 |
| `else if x == 2:` | `expected ':'` ⚠️ | 정확 |
| `1st = 3` | `invalid decimal literal` ⚠️ | 정확 |
| `my name = 3` | `invalid syntax` ⚠️ | 정확 |
| `print((1 + 2)` | `'(' was never closed` | 정확 (여는 괄호의 줄) |
| `print('hello)` | `unterminated string literal (detected at line 1)` | 정확 |
| `print（1）` (전각 괄호) | `invalid character '（' (U+FF08)` | 정확 |

⚠️는 메시지만으로 오류case를 구분할 수 없는 경우다. `else if`와 콜론 빠뜨리기는 메시지가 같고, 콜론 빠뜨리기는 if/for/while/def가 모두 같은 메시지다. 그래서 **메시지 + 오류가 난 줄의 원문**을 함께 보고 case를 정한다.

#### 매핑 표

위에서부터 차례로 검사해 처음 맞는 규칙을 쓴다.

| 조건 (메시지 / 줄 원문) | case_id | 오류case |
|---|---|---|
| `expected ':'` / 줄이 `else if`로 시작 | `cond.else_if` | else if 라고 쓰기 |
| `expected ':'` / 줄이 `if`·`elif`·`else`로 시작 | `cond.missing_colon` | 조건문 뒤에 콜론을 빠뜨리기 |
| `expected ':'` / 줄이 `for`·`while`로 시작 | `loop.missing_colon` | for 문이나 while 문 뒤에 콜론을 빠뜨리기 |
| `expected ':'` / 줄이 `def`로 시작 | `func.missing_colon` | 함수 정의 뒤에 콜론을 빠뜨리기 |
| `expected '('` / 줄이 `def`로 시작 | `func.def_missing_paren` | 함수 정의에서 괄호를 빠뜨리기 |
| `Maybe you meant '=='` 포함 | `cond.assign_in_if` | = 와 == 를 혼동하기 |
| `expected an indented block after 'if'/'elif'/'else'` | `cond.body_not_indented` | 조건문 본문을 들여쓰기 하지 않기 |
| `unindent does not match` / 줄이 `else`·`elif`로 시작 | `cond.body_not_indented` | if 와 else 의 들여쓰기 위치를 맞추지 않기 |
| `expected an indented block after 'for'/'while'` / 다음 줄이 `for`·`while` | `nested.inner_same_indent` | 안쪽 반복문을 바깥쪽과 같은 들여쓰기에 쓰기 |
| `expected an indented block after 'for'/'while'` | `loop.body_not_indented` | 반복할 문장이 반복문 안에 들어가지 않게 들여쓰기 하기 |
| `expected an indented block after function definition` | `func.body_not_indented` | 함수 본문을 들여쓰기 하지 않기 |
| `invalid decimal literal` / `=` 왼쪽이 숫자로 시작 | `var.bad_name` | 변수 이름 규칙을 어기기 (숫자로 시작) |
| `invalid syntax` / `=` 왼쪽이 공백으로 나뉜 여러 단어 | `var.bad_name` | 변수 이름 규칙을 어기기 (공백 포함) |
| `was never closed` | `syntax.unclosed_bracket` | (오류case에 없음, 추가 후보) |
| `unterminated string literal` | `syntax.unclosed_string` | (오류case에 없음, 추가 후보) |
| `invalid character` (전각 문자) | `syntax.fullwidth_char` | (오류case에 없음, 추가 후보) |
| 그 외 | `syntax.unknown` | 일반 문법 힌트 |

- 학생에게는 영어 원문 대신 case별 **우리말 단계별 힌트**를 보여 준다.
- 표에 없는 오류는 `syntax.unknown`으로 두고 줄 번호와 일반 힌트만 준다. 틀린 case로 잘못 안내하는 것보다 낫다.
- `syntax.*` 세 case는 `오류case.txt`에 없지만 초보자에게 흔하다. 목록에 추가할지 정한다.
- 메시지 문구는 파이썬 버전마다 조금씩 다르다. 매핑은 메시지 전체가 아니라 핵심 부분 문자열로 맞추고, 위 표의 실수들을 pytest 예시로 두어 버전을 올릴 때 확인한다.

#### 한계

- **한 번에 첫 번째 오류 하나만** 알려준다. 학생이 고쳐 다시 제출하면 다음 오류가 나오므로 "한 번에 1개씩" 원칙과 맞는다.
- 여러 줄에 걸친 괄호나 문자열에서는 실제 원인보다 뒤의 줄을 가리킬 수 있다. 의심 지점 문구는 "N번 줄 **근처**"처럼 여유 있게 쓴다.

### 2단계: 이름·스코프 검사

실행하면 `NameError`로 멈추는 곳이 있는지 본다. 만들기 전에 쓴 변수, 이름 오타(비슷한 이름을 후보로 기록), 정의보다 먼저 호출한 함수, `global` 없이 전역 변수 바꾸기 등을 찾는다. 예시 코드는 통과한다.

### 3단계: AST 패턴 규칙

case마다 만든 detector를 **모두** 돌려 찾은 것을 전부 모은다. 하나를 찾았다고 멈추지 않는다. 예시 코드에서 걸리는 것:

| detector | 찾은 것 | case_id |
|---|---|---|
| input 변환 없음 | `nums`의 원소는 문자열인데 4번 줄에서 숫자 `m`과 비교함 | `cond.input_str_compare` |
| 최댓값 초기값 0 | 2번 줄 `m = 0`, 4~5번 줄 `if n > m: m = n` | `loop.max_init_zero` |

### 4단계: 실행 검사 (선택, 과제가 있을 때만)

과제에 테스트케이스가 있으면 웹 서버와 **분리된 프로세스**에서 시간 제한을 두고 실행한다. 예외 종류, 시간 초과(무한 반복), 기대 출력과의 차이(경계값, 반복 횟수 ±1, 공백 개수)를 본다. 과제 기능이 없는 1차 구현에서는 건너뛴다. 예시 코드를 실행하면 4번 줄에서 `TypeError`로 멈추는데, 3단계에서 찾은 것과 같은 오류다.

### 5단계: 정리하고 고르기

1. 같은 줄, 같은 원인으로 겹친 결과를 하나로 합친다.
2. 우선순위(문법 > 실행하면 멈춤 > 무한 반복 > 결과가 틀림 > 스타일)로 정렬한다.
3. 상위 1~2개만 남긴다.

예시 코드에서는 `cond.input_str_compare`가 먼저다. `loop.max_init_zero`는 이번에는 보여 주지 않고, input 문제를 고쳐 다시 제출하면 그때 나온다.

### 6단계: 힌트 만들기와 저장 (저장은 구현됨)

고른 case의 힌트 템플릿(`cases.toml`)에 찾은 값(`{var}`, `{line}`)을 채워 단계별 힌트를 만든다.

- 관찰: "이 코드를 실행하면 비교하는 줄에서 멈춰요."
- 의심 지점: "4번 줄에서 `n`과 `m`을 비교하고 있어요. `n`에는 어떤 종류의 값이 들어 있을까요?"
- 힌트: "`input()`으로 받은 건 모두 글자예요. 숫자끼리 비교하려면 무엇을 해야 할까요?"

제출과 `findings`(case_id, 줄 번호, 전체 힌트)를 DB에 저장한다. `revealed_steps = 0`이라 응답에는 힌트 본문이 하나도 나가지 않는다. 결과가 하나도 없으면 스레드를 `fixed`로 닫는다(유지할지는 8번의 결정 사항).

### 7단계: 힌트 열기 (구현됨)

학생이 '다음 힌트'를 누를 때마다 서버가 `revealed_steps`를 1씩 올리고 그 단계까지만 보여 준다. 도우미와 짝이면 두 사람이 같은 단계를 본다.

### 8단계: 고쳐서 다시 제출

같은 `thread_id`로 제출하면 0단계부터 다시 시작하고, 서버는 이전 버전과의 줄 단위 비교를 함께 내려준다. input 문제를 고치면 `loop.max_init_zero`가 보이고, 그것까지 고치면 결과가 없어진다.

## 3. 오류case 분류 (탐지 방식별)

| 분류 | 탐지 방법 | 해당 case (예) | 개수 |
|---|---|---|---|
| **A. 문법** | `SyntaxError`/`IndentationError`의 메시지와 위치를 매핑 | 콜론 빠뜨리기(if/for/while/def), 본문 들여쓰기 안 함, `else if`, `if x = 1:`, 변수 이름 규칙 위반, 함수 정의 괄호 빠뜨리기, 안쪽 반복문을 같은 들여쓰기에 쓰기 | ~12 |
| **B. 이름·스코프** | `symtable`, pyflakes 방식의 미정의 이름 검사, `difflib`로 비슷한 이름 찾기 | 만들기 전에 사용, 이름 오타·대소문자, 정의되지 않은 함수, 함수 안 변수를 밖에서 쓰기, 정의보다 먼저 호출, `global` 없이 전역 변수 바꾸기, 같은 함수 두 번 정의, while 조건 변수 초기화 안 함, 조건이 참일 때만 변수 만들기 | ~12 |
| **C. AST 패턴** | case마다 detector 함수 하나 | `x == 1 or 2`, 범위 조건에 `or`, 넓은 조건 먼저 검사, 실수 `==`, 내장 함수 이름을 변수로 쓰기, `x + 1`만 쓰고 저장 안 함, `a = b; b = a`, `input()` 값을 변환 없이 계산, 최댓값 초기값 0, `count = 1`, `a[i]`(i는 값), `range(len(a)+1)`, 반복 변수 바꾸기, 순회 중 `remove`, 반복문 안 `return`, `while True`에 break 없음, 조건 변수가 안 바뀌는 while, 일부 경로에 return 없음, return 대신 print, return 뒤 코드, 반환값 안 받기, 인자 개수 틀림, 언패킹 개수 불일치, 재귀 종료 조건 없음, 중첩 반복에 같은 `i`, 상태 변수 초기화 안 함, `[[0]*n]*m` | ~30 |
| **D. 실행·과제 필요** | 과제의 입력/기대 출력으로 실행 비교 | 경계값 `<`/`<=`, 반복 횟수 ±1, `range` 시작·끝 오해, 한쪽 범위만 검사, 조건 빈틈, 줄바꿈 위치, 공백 개수, 행·열 반대, `range(i+1)` 연결, 인자 순서 | ~12 |
| **E. 코드에 드러나지 않는 오개념** | 정적 분석 대상 아님 → 도우미 대화 / LLM 보조 | `=`를 수학 등호로 이해, `not` 조건 반대로 이해, 전체 반복 횟수 이해, continue/break 범위 오해, 매개변수·리스트 원본 변경 오해 | ~8 |

A~C(약 55개)는 과제 정보 없이 코드만 보고 찾을 수 있으므로 1차 구현 범위로 한다. D는 **과제(문제 + 테스트케이스)** 기능이 먼저 있어야 한다. 지금 앱에는 과제 개념이 없다.

## 4. 시나리오

### 시나리오 1: 문법 (조건문 콜론 빠뜨리기)

```python
score = int(input())
if score >= 60
    print("합격")
```

- `ast.parse` 실패 → `SyntaxError: expected ':'`(2번 줄) → `cond.missing_colon`. **여기서 분석을 멈춘다.**
- 관찰: "파이썬이 코드를 읽다가 멈췄어요. 문법 규칙에 맞지 않는 곳이 있어요."
- 의심 지점: "2번 줄 `if` 문장의 끝을 보세요."
- 힌트: "`if`, `for`, `while`, `def` 줄의 끝에는 '여기서부터 본문이 시작돼요'라는 표시가 필요해요. 그 표시가 무엇이었는지 떠올려 보세요."
- 같은 `SyntaxError`라도 줄의 첫 단어(`if`/`for`/`def`)로 case를 나눠야 교사 통계에서 [조건문]/[반복문]/[함수]로 갈린다.

### 시나리오 2: 변수 (input 값을 숫자로 바꾸지 않음)

```python
age = input("나이: ")
print(age + 1)
```

- `age`가 `input()` 호출로만 대입되고 `int()`/`float()`을 거치지 않은 채 산술 연산이나 숫자와의 비교에 쓰이면 → `var.input_not_converted`.
- 관찰: "이 코드를 실행하면 계산하는 줄에서 멈춰요."
- 의심 지점: "2번 줄에서 `age`에 1을 더하고 있어요. `age`에는 어떤 종류의 값이 들어 있을까요?"
- 힌트: "`input()`은 키보드로 받은 것을 항상 **글자(문자열)**로 줘요. 숫자로 계산하려면 무엇을 해야 할까요?"
- 비교(`if age == 15`)에 쓰이면 [조건문]의 "input 문자열과 숫자를 == 로 비교하기" case로 기록한다. 탐지기는 공유하고 case_id만 다르게 둔다.

### 시나리오 3: 조건문 (`x == 1 or 2`)

```python
if menu == 1 or 2:
    print("음료")
```

- `BoolOp(Or)`의 피연산자 중 하나가 `Constant`이면 → `cond.or_constant`.
- 관찰: "`menu`에 어떤 값을 넣어도 항상 '음료'가 나와요."
- 의심 지점: "1번 줄의 `or` 뒤쪽에 있는 `2`만 따로 떼어 읽어 보세요."
- 힌트: "`or` 양쪽은 각각 참/거짓을 따로 판단해요. `2` 혼자는 참일까요, 거짓일까요?"

### 시나리오 4: 반복문 (최댓값 초기값 0)

```python
nums = [-3, -7, -1]
m = 0
for n in nums:
    if n > m:
        m = n
```

- 반복 **앞에서** `m = 0` → 반복 안에서 `if n > m: m = n`(최솟값이면 `<`) → `loop.max_init_zero`.
- 입력에 음수가 없으면 결과가 맞으므로 "의심 지점"으로 표현한다.
- 관찰: "`m`은 처음에 0에서 시작해요."
- 의심 지점: "리스트의 수가 모두 음수라면 4번 줄 `if`는 한 번이라도 참이 될까요?"
- 힌트: "최댓값의 처음 값은 '아직 아무것도 안 본 상태'를 나타내야 해요. 리스트 안에 있는 값으로 시작하면 어떨까요?"

### 시나리오 5: 무한 반복 (while 조건 변수를 바꾸지 않음)

```python
i = 1
while i <= 10:
    print(i)
```

- `while` 조건에 쓰인 이름이 본문에서 대입되지 않고 `break`/`return`도 없으면 → `loop.while_no_update`.
- 실행 검사를 붙이면 타임아웃으로도 잡히지만, 정적으로 먼저 잡으면 실행할 필요가 없다.
- 관찰: "이 반복문은 끝나지 않아요."
- 의심 지점: "반복할 때마다 `i`의 값은 어떻게 바뀌나요?"
- 힌트: "조건이 언젠가 거짓이 되려면 반복문 안에서 무엇인가 바뀌어야 해요."

### 시나리오 6: 함수 (return 대신 print)

```python
def add(a, b):
    print(a + b)

total = add(3, 4) * 2
```

- 함수에 `return 값`이 하나도 없는데 호출 결과를 대입하거나 계산에 쓰면 → `func.print_not_return`(실행하면 `None * 2`에서 TypeError).
- 관찰: "화면에 7은 나오지만 그다음 줄에서 멈춰요."
- 의심 지점: "`add(3, 4)`가 끝난 뒤 그 자리에는 어떤 값이 남을까요?"
- 힌트: "`print`는 화면에 보여 주기만 하고, 값을 돌려주는 건 다른 키워드예요."
- "return이 없는 함수의 결과(None)를 계산에 쓰기"와 탐지 조건이 거의 같다. 함수 안에 `print`가 있으면 print/return 혼동, 없으면 None 사용으로 나눈다.

### 시나리오 7: 중첩 반복문 (소수 판별 상태 변수)

```python
is_prime = True
for n in range(2, 20):
    for d in range(2, n):
        if n % d == 0:
            is_prime = False
    if is_prime:
        print(n)
```

- 안쪽 반복에서 `False`로 바뀌는 변수가 바깥 반복 **밖에서만** 초기화되고 바깥 반복 본문에서 다시 초기화되지 않으면 → `nested.flag_not_reset`.
- 관찰: "`is_prime`은 1번 줄에서 한 번만 True가 돼요."
- 의심 지점: "n이 4일 때 False가 된 뒤, n이 5가 되면 `is_prime`은 무엇일까요?"
- 힌트: "숫자마다 새로 검사하려면 '검사를 시작하는 곳'에서 상태를 새로 정해야 해요."

### 시나리오 8: 고치는 과정 (스레드 연결)

1. 1차 제출: 문법 오류 → case 1개. 학생이 힌트 2단계까지 연다.
2. 2차 제출(같은 `thread_id`): 문법 통과, `var.input_not_converted` + `loop.max_init_zero` → 우선순위에 따라 **input 쪽만** 보여 준다.
3. 3차 제출: input 해결 → 이번에는 max 초기값이 보인다.
4. 4차 제출: 결과 없음 → 스레드 종료(8번의 결정 사항 참고).

대시보드는 이 학생이 case 3개를 만났고 4번 만에 고쳤다고 집계한다. 지금 `app/stats.py`의 오류 유형·중앙값 계산이 그대로 동작한다.

## 5. 데이터 형식: `오류case.txt` → `cases.toml`

`tomllib`는 표준 라이브러리(3.11+)라 의존성이 늘지 않는다.

```toml
[[case]]
id = "cond.or_constant"
category = "조건문"                 # 대시보드 묶음
label = "x == 1 or 2 처럼 or 조건을 잘못 쓰기"   # CASE_LABELS 대체
method = "ast"                      # syntax | scope | ast | exec | none
priority = 40
steps = [
  { label = "관찰",      text = "`{var}`에 어떤 값을 넣어도 항상 같은 쪽이 실행돼요." },
  { label = "의심 지점", text = "{line}번 줄의 `or` 뒤쪽만 따로 떼어 읽어 보세요." },
  { label = "힌트",      text = "`or` 양쪽은 각각 참/거짓을 따로 판단해요. `{const}` 혼자는 참일까요?" },
]
bad  = ["if menu == 1 or 2:\n    print('x')"]
good = ["if menu == 1 or menu == 2:\n    print('x')"]
```

- `id`는 `Finding.case_id`, 힌트 문구, 교사 통계(FR-3)를 잇는 키다. id 형식은 `분류.이름`(`var.`, `cond.`, `loop.`, `func.`, `nested.`)으로 한다.
- `{var}`, `{line}`, `{const}`는 detector가 찾은 값으로 채운다.
- `bad`/`good` 예시는 pytest가 매개변수로 읽어 **모든 case가 bad는 잡고 good은 안 잡는지** 자동 검증한다. 오탐을 막는 가장 싼 장치다.
- `method = "none"`(E 분류)인 case도 목록에 둔다. 나중에 도우미 채팅이나 LLM이 쓸 대화용 힌트를 붙일 자리다.

## 6. 코드 구조

```
app/analysis.py          analyze(), Finding, HintStep (공개 인터페이스는 그대로)
app/analyzer/
  cases.toml             위 목록
  catalog.py             TOML 로드 → CASE_LABELS, 단계 템플릿
  syntax.py              SyntaxError 메시지·줄 → case_id 매핑
  scope.py               미정의 이름, 오타 추천, global, 정의 순서
  rules/cond.py, loop.py, func.py, var.py, nested.py
                         @rule("cond.or_constant") def ...(tree) -> list[Match]
  runner.py              (D단계) subprocess + 타임아웃 + 리소스 제한
```

`analyze()`는 ① syntax → ② scope → ③ rules를 돌려 `Match(case_id, line, vars)`를 모으고, priority로 정렬한 뒤 상위 N개를 `Finding`으로 바꾼다.

## 7. 구현 순서

1. `cases.toml` 작성: 80개에 id·분류·method를 붙인다. 먼저 사용자 확인이 필요한 두 항목("합계를 반복문 밖에서 초기화하기", "함수 이름에 괄호를 사용하지 않기")부터 정리한다.
2. A(문법): 효과는 가장 크고 오탐은 거의 없다.
3. B(이름·스코프): 실행하면 멈추는 오류들이다.
4. C(AST 패턴): 자주 나오는 것부터 10개 정도 만들고 이후 확장한다.
5. D(실행): 과제 기능을 만들 때 함께 한다.

## 8. 결정이 필요한 것

- **분석 결과가 비면 스레드를 자동으로 fixed로 할지.** 미탐을 택하므로 결과가 비어도 정답이라는 보장은 없다. "찾은 문제는 없어요. 직접 확인했으면 '다 고쳤어요'를 눌러 주세요"로 바꾸는 편이 안전하다.
- **과제 기능을 만들지.** D 분류 약 12개는 과제별 기대 출력 없이는 찾을 수 없다.
- **E 분류(오개념)를 어떻게 다룰지.** 정적 분석 대상에서 빼고 도우미 채팅이나 LLM(FR-2.2) 쪽으로 넘길지 정한다.
- **한 번에 보여 줄 Finding 개수** (1개 또는 2개).
