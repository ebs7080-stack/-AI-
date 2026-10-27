from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "코딩입문 중고등학생을 위한 단계적 피드백 웹서비스" in r.text


def test_warns_when_python_version_differs(tmp_path, monkeypatch, caplog):
    """문법 오류 분류는 파이썬 3.12 의 메시지에 맞췄다. 다른 버전으로 켜면 로그로 알린다."""
    from app import main

    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(main, "SUPPORTED_PYTHON", (2, 7))
    with TestClient(app):
        pass
    assert "test_syntax.py" in caplog.text


def test_no_warning_on_supported_python(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app):
        pass
    assert "test_syntax.py" not in caplog.text


def test_requirements_file_is_ascii():
    """한국어 윈도우의 pip 는 requirements.txt 를 cp949 로 읽어서, 한글 주석이 있으면 설치가 멈춘다."""
    from pathlib import Path

    (Path(__file__).resolve().parent.parent / "requirements.txt").read_bytes().decode("ascii")
