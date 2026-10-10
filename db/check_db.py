"""data.db 가 최신 스키마(v4)로 제대로 올라갔는지 점검한다. 읽기만 하고 아무것도 바꾸지 않는다.

    .venv\\Scripts\\python db\\check_db.py                # 프로젝트 루트의 data.db
    .venv\\Scripts\\python db\\check_db.py path\\to\\x.db   # 다른 파일

서버가 켜져 있어도 실행할 수 있다. 문제가 있으면 마지막 줄에 ❌ 가 나오고 종료 코드가 1 이다.
"""

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE.parent / "data.db"
LATEST = 4

TABLES = {
    "classes", "students", "sessions", "teacher_sessions", "threads", "submissions", "findings",
    "help_sessions", "messages", "units", "error_cases", "hint_reveals", "escalations",
    "schema_version", "teachers", "teacher_account_sessions",
}
VIEWS = {
    "v_class_case_stats", "v_unit_case_stats", "v_submission_result", "v_student_case_rank",
    "v_class_case_rank", "v_student_unit_progress", "v_class_unit_progress", "v_student_stats",
    "v_class_overview",
}
TRIGGERS = {"trg_classes_current_unit_ins", "trg_classes_current_unit_upd", "trg_threads_default_unit"}
INDEXES = {
    "one_active_help_per_thread", "one_active_help_per_helper", "idx_threads_unit_status",
    "idx_classes_teacher", "idx_teacher_account_sessions_teacher",
}
# (표, 꼭 있어야 하는 열)
COLUMNS = {
    "classes": {"teacher_id", "current_unit_id"},
    "units": {"status", "target_fixed"},
    "threads": {"unit_id"},
    "help_sessions": {"helper_kind"},
    "messages": {"sender_kind"},
}

problems: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("✅ " if ok else "❌ ") + label + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        problems.append(label)


def names(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'", (kind,))
    return {r[0] for r in rows}


def main(path: Path) -> int:
    if not path.exists():
        print(f"❌ {path} 파일이 없어요. 서버(start.bat)를 한 번 켜면 만들어져요.")
        return 1
    print(f"점검 대상: {path}\n")
    # 읽기 전용으로 연다 (서버가 켜져 있어도 안전하다).
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        has_version = "schema_version" in names(conn, "table")
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] if has_version else 1
        check(version == LATEST, f"스키마 버전 v{LATEST}", f"지금은 v{version}. 서버를 한 번 껐다 켜거나 db\\migrate.py 를 실행하세요")

        for label, expected, found in (
            ("표", TABLES, names(conn, "table")),
            ("뷰", VIEWS, names(conn, "view")),
            ("트리거", TRIGGERS, names(conn, "trigger")),
            ("인덱스", INDEXES, names(conn, "index")),
        ):
            missing = sorted(expected - found)
            check(not missing, f"{label} {len(expected)}개 모두 있음", "빠진 것: " + ", ".join(missing))

        for table, cols in COLUMNS.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            check(cols <= have, f"{table} 표의 새 열: {', '.join(sorted(cols))}", "빠진 것: " + ", ".join(sorted(cols - have)))

        # 뷰가 실제로 조회되는지 (정의가 깨졌으면 여기서 에러가 난다)
        broken = []
        for view in sorted(names(conn, "view")):
            try:
                conn.execute(f"SELECT * FROM {view} LIMIT 1").fetchall()
            except sqlite3.Error as err:
                broken.append(f"{view}: {err}")
        check(not broken, "모든 뷰가 조회됨", "; ".join(broken))

        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        check(not fk, "외래키(표 사이 연결) 오류 없음", f"{len(fk)}건")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        check(integrity == "ok", "DB 파일 무결성", integrity)

        print("\n지금 들어 있는 데이터 개수")
        for table in ("teachers", "classes", "units", "students", "threads", "submissions", "findings", "help_sessions", "messages"):
            if table in names(conn, "table"):
                print(f"  {table:<12} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")

        orphans = conn.execute(
            "SELECT COUNT(*) FROM classes WHERE teacher_id IS NULL"
        ).fetchone()[0] if "teacher_id" in {r[1] for r in conn.execute("PRAGMA table_info(classes)")} else 0
        if orphans:
            print(f"\n참고: 교사 계정에 연결되지 않은 학급이 {orphans}개 있어요 (교사 키로 만든 옛 학급). "
                  "classes.html 의 '이미 만든 학급 가져오기'로 연결할 수 있어요.")
    finally:
        conn.close()

    print()
    if problems:
        print(f"❌ 문제 {len(problems)}개. 위의 ❌ 줄을 그대로 보내 주세요.")
        return 1
    print("✅ 모두 정상이에요.")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB))
