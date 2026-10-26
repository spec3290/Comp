"""내 정보·프로필 설정 (F1-7, Q-5 기본값)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.repository import UserRepository
from app.auth.rules import InterestField, ProfileInput, SchoolType, normalize_nickname
from app.auth.service import NicknameTakenError, UserService
from tests.unit.auth_support import bearer, login

PROFILE = {
    "nickname": "코딩왕",
    "grade": 2,
    "school_type": "대학교",
    "interest_fields": ["AI", "개발"],
}


def _put(client: TestClient, access: object, **changes: object) -> tuple[int, dict[str, object]]:
    response = client.put("/users/me/profile", headers=bearer(access), json={**PROFILE, **changes})
    body: dict[str, object] = response.json()
    return response.status_code, body


def _error_code(body: dict[str, object]) -> object:
    error = body["error"]
    assert isinstance(error, dict)
    return error["code"]


@pytest.mark.feature("F1-7")
def test_new_user_me_shows_empty_profile_and_needs_onboarding(client: TestClient) -> None:
    access = login(client)["access_token"]

    response = client.get("/users/me", headers=bearer(access))

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "google"
    assert body["needs_onboarding"] is True
    assert body["nickname"] is None and body["school_type"] is None and body["grade"] is None
    assert body["interest_fields"] == []
    assert "provider_id" not in body  # 제공자 식별자는 내보내지 않는다
    assert body["created_at"].endswith("+09:00")  # KST 로 응답한다


@pytest.mark.feature("F1-7")
def test_me_requires_access_token(client: TestClient) -> None:
    response = client.get("/users/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-7")
def test_me_after_logout_returns_401(client: TestClient) -> None:
    access = login(client)["access_token"]
    client.post("/auth/logout", headers=bearer(access))

    assert client.get("/users/me", headers=bearer(access)).status_code == 401


@pytest.mark.feature("F1-7")
def test_saving_profile_completes_onboarding(client: TestClient) -> None:
    access = login(client)["access_token"]

    status, body = _put(client, access)

    assert status == 200
    assert body["nickname"] == "코딩왕"
    assert body["grade"] == 2
    assert body["school_type"] == "대학교"
    assert body["interest_fields"] == ["개발", "AI"]  # 어휘 정의 순서
    assert body["needs_onboarding"] is False
    assert client.get("/users/me", headers=bearer(access)).json() == body
    assert login(client)["needs_onboarding"] is False


@pytest.mark.feature("F1-7")
def test_profile_update_replaces_interest_fields(client: TestClient) -> None:
    access = login(client)["access_token"]
    _put(client, access, interest_fields=["AI", "보안"])

    _, changed = _put(client, access, interest_fields=["보안", "디자인", "보안"])
    _, cleared = _put(client, access, interest_fields=[])

    assert changed["interest_fields"] == ["보안", "디자인"]
    assert cleared["interest_fields"] == []


@pytest.mark.feature("F1-7")
def test_grade_is_optional(client: TestClient) -> None:
    status, body = _put(client, login(client)["access_token"], grade=None, school_type="기타")

    assert status == 200
    assert body["grade"] is None


@pytest.mark.feature("F1-7")
@pytest.mark.parametrize(
    "changes",
    [
        {"nickname": "가"},
        {"nickname": "가" * 13},
        {"nickname": "   "},
        {"nickname": "탭\t문자"},
        {"grade": 0},
        {"grade": 7},
        {"school_type": "초등학교"},
        {"interest_fields": ["요리"]},
        {"nickname": None},
    ],
    ids=[
        "nickname-1",
        "nickname-13",
        "nickname-blank",
        "nickname-control",
        "grade-0",
        "grade-7",
        "school-type",
        "interest-field",
        "nickname-missing",
    ],
)
def test_invalid_profile_returns_400(client: TestClient, changes: dict[str, object]) -> None:
    status, body = _put(client, login(client)["access_token"], **changes)

    assert status == 400
    assert _error_code(body) == "VALIDATION_ERROR"


@pytest.mark.feature("F1-7")
@pytest.mark.parametrize("nickname", ["가나", "가" * 12, "  공백포함 닉  "])
def test_nickname_length_boundaries_are_accepted(client: TestClient, nickname: str) -> None:
    status, body = _put(client, login(client)["access_token"], nickname=nickname)

    assert status == 200
    assert body["nickname"] == nickname.strip()


@pytest.mark.feature("F1-7")
def test_nickname_taken_by_other_user_returns_409(client: TestClient) -> None:
    _put(client, login(client, "code-a")["access_token"])

    status, body = _put(client, login(client, "code-b")["access_token"])

    assert status == 409
    assert _error_code(body) == "NICKNAME_TAKEN"


@pytest.mark.feature("F1-7")
def test_user_can_keep_own_nickname(client: TestClient) -> None:
    access = login(client)["access_token"]
    _put(client, access)

    status, _ = _put(client, access, grade=3)

    assert status == 200


class SlowCheckRepository(UserRepository):
    """중복 확인은 통과했는데 저장 직전에 다른 회원이 같은 닉네임을 가져간 상황."""

    def nickname_taken(self, nickname: str, exclude_user_id: int) -> bool:
        return False


@pytest.mark.feature("F1-7")
def test_concurrent_same_nickname_returns_409(client: TestClient, session: Session) -> None:
    login(client, "code-a")
    login(client, "code-b")
    first, second = session.scalars(select(User).order_by(User.id)).all()
    service = UserService(SlowCheckRepository(session))
    profile = ProfileInput("코딩왕", 1, SchoolType.HIGH, frozenset({InterestField.AI}))
    service.update_profile(first.id, profile)

    with pytest.raises(NicknameTakenError):
        service.update_profile(second.id, profile)
    session.refresh(second)
    assert second.nickname is None  # 실패한 저장은 되돌려진다


@pytest.mark.feature("F1-7")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  코딩왕 ", "코딩왕"), ("ab", "ab"), ("한글 닉네임", "한글 닉네임")],
)
def test_normalize_nickname_strips_spaces(raw: str, expected: str) -> None:
    assert normalize_nickname(raw) == expected
