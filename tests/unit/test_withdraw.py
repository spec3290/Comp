"""회원 탈퇴 API (F1-8). 재인증으로 받은 제공자 토큰으로 연결을 해제한 뒤 회원을 지운다 (Q-25).

연결 해제가 실패하면 탈퇴하지 않는다 (Q-7). fixture 는 conftest.py 에 있다.
"""

import logging
from urllib.parse import parse_qs, urlsplit

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import UserInterestField
from app.auth.oauth import OAuthFailedError, OAuthUnavailableError
from app.auth.service import WITHDRAW_STATE_TTL
from app.contests.models import Bookmark
from app.core.time import now_kst
from tests.unit.auth_support import (
    WITHDRAW_FRONTEND_URL,
    FakeProvider,
    bearer,
    claims,
    login,
    user_count,
)

PROFILE = {"nickname": "탈퇴할회원", "school_type": "대학교", "interest_fields": ["AI"]}


def _start(client: TestClient, access: object) -> str:
    """탈퇴를 시작하고 제공자 재인증 주소에 실린 state 를 돌려준다."""
    response = client.post("/users/me/withdrawal", headers=bearer(access))
    assert response.status_code == 200, response.text
    url: str = response.json()["redirect_url"]
    return parse_qs(urlsplit(url).query)["state"][0]


def _callback(client: TestClient, state: str | None, code: str | None = "code-a") -> dict[str, str]:
    """재인증 콜백을 부르고 프론트로 가는 리다이렉트의 쿼리를 돌려준다."""
    params = {k: v for k, v in {"code": code, "state": state}.items() if v is not None}
    response = client.get("/auth/google/withdraw/callback", params=params, follow_redirects=False)
    assert response.status_code == 302, response.text
    assert response.headers["cache-control"] == "no-store"
    location = response.headers["location"]
    assert location.startswith(f"{WITHDRAW_FRONTEND_URL}?"), location
    return {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}


def _count(session: Session, model: type[Bookmark] | type[UserInterestField]) -> int:
    """테이블의 행 수."""
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.feature("F1-8")
def test_withdrawal_start_returns_reauth_url_with_one_time_state(
    client: TestClient, redis: fakeredis.FakeRedis
) -> None:
    """탈퇴 시작은 계정 선택을 다시 거치는 재인증 주소를 주고, state 를 회원과 묶어 짧게 둔다."""
    tokens = login(client)

    response = client.post("/users/me/withdrawal", headers=bearer(tokens["access_token"]))

    assert response.status_code == 200
    url = response.json()["redirect_url"]
    assert "prompt=select_account" in url
    state = parse_qs(urlsplit(url).query)["state"][0]
    assert len(state) >= 32
    assert redis.get(f"withdraw_state:{state}") == claims(tokens["access_token"])["sub"]
    assert 0 < redis.ttl(f"withdraw_state:{state}") <= WITHDRAW_STATE_TTL.total_seconds()


@pytest.mark.feature("F1-8")
def test_withdrawal_revokes_provider_and_deletes_user_with_personal_data(
    client: TestClient, session: Session, provider: FakeProvider
) -> None:
    """재인증한 계정이 맞으면 연결을 해제하고 회원·프로필·관심 분야·북마크를 지운다."""
    tokens = login(client)
    headers = bearer(tokens["access_token"])
    assert client.put("/users/me/profile", json=PROFILE, headers=headers).status_code == 200
    user_id = int(str(claims(tokens["access_token"])["sub"]))
    session.add(Bookmark(user_id=user_id, contest_id="c" * 24, created_at=now_kst()))
    session.commit()

    params = _callback(client, _start(client, tokens["access_token"]))

    assert params == {"withdrawn": "true"}
    assert provider.revoked == ["provider-token-code-a"]
    assert user_count(session) == 0
    assert _count(session, UserInterestField) == 0
    assert _count(session, Bookmark) == 0


@pytest.mark.feature("F1-8")
def test_tokens_of_every_session_are_rejected_after_withdrawal(
    client: TestClient, protected: TestClient
) -> None:
    """탈퇴하면 다른 기기 로그인까지 모든 Access·Refresh 가 401 `TOKEN_INVALID` 다."""
    here, other = login(client), login(client)

    _callback(client, _start(client, here["access_token"]))

    for tokens in (here, other):
        for response in (
            protected.get("/protected", headers=bearer(tokens["access_token"])),
            client.get("/users/me", headers=bearer(tokens["access_token"])),
            client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}),
        ):
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-8")
def test_login_after_withdrawal_creates_new_account(client: TestClient) -> None:
    """탈퇴한 계정으로 다시 로그인하면 온보딩이 필요한 새 회원이고 토큰이 정상 동작한다."""
    _callback(client, _start(client, login(client)["access_token"]))

    again = login(client)

    assert again["needs_onboarding"] is True
    assert client.get("/users/me", headers=bearer(again["access_token"])).status_code == 200


@pytest.mark.feature("F1-8")
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OAuthUnavailableError("장애"), "OAUTH_UNAVAILABLE"),
        (OAuthFailedError("거절"), "OAUTH_FAILED"),
    ],
    ids=["provider-outage", "provider-rejected"],
)
def test_revoke_failure_keeps_account(
    client: TestClient,
    session: Session,
    provider: FakeProvider,
    error: Exception,
    expected: str,
) -> None:
    """연결 해제가 실패하면 탈퇴를 중단하고 회원과 토큰을 그대로 둔다 (Q-7)."""
    tokens = login(client)
    provider.revoke_error = error

    params = _callback(client, _start(client, tokens["access_token"]))

    assert params == {"error": expected}
    assert user_count(session) == 1
    assert client.get("/users/me", headers=bearer(tokens["access_token"])).status_code == 200


@pytest.mark.feature("F1-8")
def test_reauth_with_other_account_does_not_withdraw_or_revoke(
    client: TestClient, session: Session, provider: FakeProvider
) -> None:
    """재인증에서 다른 계정을 고르면 `OAUTH_ACCOUNT_MISMATCH` 이고 어느 연결도 끊지 않는다."""
    state = _start(client, login(client)["access_token"])

    params = _callback(client, state, code="code-b")

    assert params == {"error": "OAUTH_ACCOUNT_MISMATCH"}
    assert provider.revoked == []
    assert user_count(session) == 1


@pytest.mark.feature("F1-8")
def test_cancelled_reauth_keeps_account(client: TestClient, session: Session) -> None:
    """재인증 화면에서 취소해 code 없이 돌아오면 `OAUTH_FAILED` 이고 회원은 남는다."""
    state = _start(client, login(client)["access_token"])

    assert _callback(client, state, code=None) == {"error": "OAUTH_FAILED"}
    assert user_count(session) == 1


@pytest.mark.feature("F1-8")
@pytest.mark.parametrize("state", [None, "unknown-state"], ids=["missing", "unknown"])
def test_callback_without_valid_state_does_not_withdraw(
    client: TestClient, session: Session, provider: FakeProvider, state: str | None
) -> None:
    """탈퇴 시작 없이 온 콜백은 `OAUTH_STATE_MISMATCH` 이고 제공자 코드도 쓰지 않는다."""
    login(client)

    assert _callback(client, state) == {"error": "OAUTH_STATE_MISMATCH"}
    assert provider.revoked == []
    assert user_count(session) == 1


@pytest.mark.feature("F1-8")
def test_withdraw_state_can_be_used_only_once(client: TestClient, session: Session) -> None:
    """실패한 콜백도 state 를 소비해, 같은 state 로 다시 와도 탈퇴하지 않는다."""
    state = _start(client, login(client)["access_token"])
    _callback(client, state, code="code-b")

    assert _callback(client, state) == {"error": "OAUTH_STATE_MISMATCH"}
    assert user_count(session) == 1


@pytest.mark.feature("F1-8")
def test_withdrawal_start_without_token_returns_401(client: TestClient) -> None:
    """탈퇴 시작에는 Access 가 필요하다."""
    response = client.post("/users/me/withdrawal")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-8")
def test_withdrawal_does_not_log_provider_token(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """앱 로그에 제공자 토큰과 인가 코드가 남지 않는다 (AGENTS.md 하드 규칙).

    접근 로그의 쿼리 값은 app.core.log_redaction 이 가리므로 여기서는 앱 로거만 본다.
    """
    state = _start(client, login(client)["access_token"])

    with caplog.at_level(logging.DEBUG):
        _callback(client, state)

    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app."))
    assert "회원 탈퇴" in text
    assert "provider-token" not in text
    assert "code-a" not in text
