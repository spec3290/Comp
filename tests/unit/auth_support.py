"""인증 테스트 공용 도구. fixture 는 conftest.py 에 있다."""

from collections.abc import Mapping
from datetime import timedelta
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.oauth import OAuthFailedError, OAuthIdentity, OAuthUnavailableError
from app.auth.tokens import JWT_ALGORITHM

SECRET = "test-secret-at-least-32-bytes-long!!"
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=14)
CODES = {"code-a": "google-sub-a", "code-b": "google-sub-b"}
OUTAGE_CODE = "outage"  # FakeProvider 가 제공자 장애로 응답하는 코드
FRONTEND_URL = "http://frontend.test/auth/callback"


class HttpResponse(Protocol):
    """TestClient 응답 중 여기서 쓰는 부분. TestClient 가 쓰는 httpx 패키지에 묶이지 않는다."""

    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def text(self) -> str: ...


class FakeProvider:
    """인가 코드 → 식별자 표. 표에 없는 코드는 제공자가 거절한 것으로 본다. OUTAGE_CODE 는 장애."""

    name = "google"

    def __init__(self, codes: dict[str, str], redirect_uri: str = "http://testserver/cb") -> None:
        self.codes = codes
        self.redirect_uri = redirect_uri

    def authorization_url(self, state: str) -> str:
        return f"https://accounts.example.com/auth?state={state}"

    def exchange_code(self, code: str) -> OAuthIdentity:
        if code == OUTAGE_CODE:
            raise OAuthUnavailableError("Google 로그인 서버에 연결할 수 없습니다.")
        if code not in self.codes:
            raise OAuthFailedError("Google 로그인에 실패했습니다.")
        return OAuthIdentity(provider=self.name, provider_id=self.codes[code])


def start_state(client: TestClient) -> str:
    """로그인을 시작하고 제공자로 보낸 state 를 돌려준다 (쿠키에도 같은 값이 남는다)."""
    start = client.get("/auth/google/login", follow_redirects=False)
    return parse_qs(urlsplit(start.headers["location"]).query)["state"][0]


def callback(client: TestClient, code: str = "code-a") -> HttpResponse:
    """로그인 시작 → 제공자에서 돌아온 콜백까지. 프론트로 가는 리다이렉트 응답을 돌려준다."""
    state = start_state(client)
    return client.get(
        "/auth/google/callback", params={"code": code, "state": state}, follow_redirects=False
    )


def frontend_params(response: HttpResponse) -> dict[str, str]:
    """프론트로 가는 리다이렉트의 쿼리. 주소가 FRONTEND_URL 인지도 확인한다."""
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{FRONTEND_URL}?"), location
    return {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}


def login(client: TestClient, code: str = "code-a") -> dict[str, object]:
    """콜백 → 프론트가 login_code 를 교환하기까지. 교환 응답 본문을 돌려준다."""
    login_code = frontend_params(callback(client, code))["login_code"]
    response = client.post("/auth/token", json={"login_code": login_code})
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
