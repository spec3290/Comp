"""헬스 체크 테스트."""

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    """/health 가 200 과 ok 를 돌려준다."""
    res = TestClient(app).get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
