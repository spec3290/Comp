"""인증 테스트 공용 도구. fixture 는 conftest.py 에 있다."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.oauth import OAuthFailedError, OAuthIdentity
from app.auth.tokens import JWT_ALGORITHM

SECRET = "test-secret-at-least-32-bytes-long!!"
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=14)
CODES = {"code-a": "google-sub-a", "code-b": "google-sub-b"}


class FakeProvider:
    """인가 코드 → 식별자 표. 표에 없는 코드는 제공자가 거절한 것으로 본다."""

    name = "google"

    def __init__(self, codes: dict[str, str], redirect_uri: str = "http://testserver/cb") -> None:
        self.codes = codes
        self.redirect_uri = redirect_uri

    def authorization_url(self, state: str) -> str:
        return f"https://accounts.example.com/auth?state={state}"

    def exchange_code(self, code: str) -> OAuthIdentity:
        if code not in self.codes:
            raise OAuthFailedError("Google 로그인에 실패했습니다.")
        return OAuthIdentity(provider=self.name, provider_id=self.codes[code])


def login(client: TestClient, code: str = "code-a") -> dict[str, object]:
    """로그인 시작 → 제공자에서 돌아온 콜백까지. 콜백 응답 본문을 돌려준다."""
    start = client.get("/auth/google/login", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = client.get("/auth/google/callback", params={"code": code, "state": state})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def claims(token: object) -> dict[str, object]:
    assert isinstance(token, str)
    decoded: dict[str, object] = jwt.decode(token, SECRET, algorithms=[JWT_ALGORITHM])
    return decoded


def bearer(token: object) -> dict[str, str]:
    assert isinstance(token, str)
    return {"Authorization": f"Bearer {token}"}


def user_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User)) or 0
