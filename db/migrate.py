"""data.db 를 최신 스키마로 올린다 (v1 -> v2 -> v3 를 순서대로, 필요한 것만).

    python db/migrate.py                 # 프로젝트 루트의 data.db 를 올린다
    python db/migrate.py path/to/x.db    # 다른 파일을 올린다
    python db/migrate.py --new x.db      # 없는 파일에 새 DB 를 만든다 (schema_v2.sql + 이후 단계)

이미 최신이면 아무것도 하지 않는다. 올리기 전에 파일을 .bak 으로 복사해 둔다.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE.parent / "data.db"

# (올라가는 버전, 파일). v1 -> v2 는 v1 DB 에만 쓰고, 새 DB 는 schema_v2.sql 에서 시작한다.
STEPS = [
    (2, "migrate_v1_to_v2.sql"),
    (3, "migrate_v2_to_v3.sql"),
    (4, "migrate_v3_to_v4.sql"),
]
LATEST = STEPS[-1][0]


def current_version(conn: sqlite3.Connection) -> int:
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not has_table:
        return 1
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 1


def apply_pending(conn: sqlite3.Connection) -> None:
    """아직 적용하지 않은 단계만 순서대로 적용한다. 서버 시작(app/db.py)도 이 함수를 쓴다."""
    for version, name in STEPS:
        if current_version(conn) >= version:
            continue
        conn.executescript((HERE / name).read_text(encoding="utf-8"))
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"{name}: 외래키 검사 실패: {problems[:5]}")


def create(path: Path) -> str:
    if path.exists():
        return f"{path} 가 이미 있습니다. 올리려면 --new 없이 실행하세요."
    conn = sqlite3.connect(path)
    try:
        conn.executescript((HERE / "schema_v2.sql").read_text(encoding="utf-8"))
        apply_pending(conn)
    finally:
        conn.close()
    return f"새 DB 를 v{LATEST} 로 만들었습니다."


def migrate(path: Path, backup: bool = True) -> str:
    if not path.exists():
        return f"{path} 가 없습니다. 새 DB 는 --new 로 만드세요."
    conn = sqlite3.connect(path)
    try:
        before = current_version(conn)
    finally:
        conn.close()
    if before >= LATEST:
        return f"이미 v{LATEST} 입니다."

    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    conn = sqlite3.connect(path)
    try:
        apply_pending(conn)
    finally:
        conn.close()
    return f"v{before} -> v{LATEST} 로 올렸습니다."


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--new":
        print(create(Path(args[1])))
    else:
        print(migrate(Path(args[0]) if args else DEFAULT_DB))
