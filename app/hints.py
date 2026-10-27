"""교사가 고쳐 쓴 오류별 힌트 문구.

기본 문구는 app/analyzer/cases.toml 에 있고, 교사가 고친 case 만 teacher_hints 테이블에 따로 둔다.
교사 계정에 묶여 있어서 그 교사의 모든 교실에 적용된다. 고친 문구는 그 뒤의 새 제출부터 쓰인다
(이미 제출된 코드의 힌트는 제출할 때 findings.steps_json 에 굳어 있다).
"""

import json
import sqlite3

from .analyzer import catalog

MAX_STEPS = 6
MAX_LABEL_LENGTH = 20
MAX_TEXT_LENGTH = 500


def _custom(db: sqlite3.Connection, teacher_id: int) -> dict[str, list[dict]]:
    """그 교사가 고친 case_id -> 단계 문구 틀."""
    rows = db.execute(
        "SELECT case_id, steps_json FROM teacher_hints WHERE teacher_id = ?", (teacher_id,)
    ).fetchall()
    return {row["case_id"]: json.loads(row["steps_json"]) for row in rows}


def custom_for_class(db: sqlite3.Connection, class_id: int) -> dict[str, list[dict]]:
    """그 교실의 교사가 고친 문구. 교사 계정이 없는 예전 교실은 고친 것이 없다."""
    row = db.execute("SELECT teacher_id FROM classes WHERE id = ?", (class_id,)).fetchone()
    if row is None or row["teacher_id"] is None:
        return {}
    return _custom(db, row["teacher_id"])


def _case_out(case: dict, custom: list[dict] | None) -> dict:
    default = [{"label": s["label"], "text": s["text"]} for s in case["steps"]]
    return {
        "id": case["id"],
        "category": case["category"],
        "label": case["label"],
        # 이 case 에서 분석기가 채워 줄 수 있는 자리. 기본 문구가 쓰는 것들이다.
        "placeholders": [
            {"name": name, "meaning": catalog.PLACEHOLDERS[name]}
            for name in catalog.placeholders_in(default)
        ],
        "default_steps": default,
        "steps": custom if custom is not None else default,
        "customized": custom is not None,
    }


def list_cases(db: sqlite3.Connection, teacher_id: int) -> list[dict]:
    custom = _custom(db, teacher_id)
    return [_case_out(case, custom.get(case_id)) for case_id, case in catalog.cases().items()]


def get_case(db: sqlite3.Connection, teacher_id: int, case_id: str) -> dict | None:
    case = catalog.cases().get(case_id)
    if case is None:
        return None
    return _case_out(case, _custom(db, teacher_id).get(case_id))


def save(db: sqlite3.Connection, teacher_id: int, case_id: str, steps: list[dict]) -> None:
    db.execute(
        """
        INSERT INTO teacher_hints (teacher_id, case_id, steps_json) VALUES (?, ?, ?)
        ON CONFLICT (teacher_id, case_id)
        DO UPDATE SET steps_json = excluded.steps_json, updated_at = datetime('now')
        """,
        (teacher_id, case_id, json.dumps(steps, ensure_ascii=False)),
    )


def reset(db: sqlite3.Connection, teacher_id: int, case_id: str) -> None:
    """기본 문구로 되돌린다."""
    db.execute(
        "DELETE FROM teacher_hints WHERE teacher_id = ? AND case_id = ?", (teacher_id, case_id)
    )
