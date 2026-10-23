"""CI가 쓰는 Git 규약 검사기 자체를 검증한다 (PDF 'Github Commit 명세'의 예시 포함)."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_conventions.py"
    spec = importlib.util.spec_from_file_location("check_conventions", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


conv = _load()


@pytest.mark.parametrize(
    "message",
    [
        "🐛 :: [MainPage] 헤더가 안보이는 버그 수정",
        "🐛 :: (#19)[FeedServiceImpl] 피드 버그 로직 수정",
        "🚀 :: v1.0.0",
        "📝 :: README 파일 수정",
        "🔀 :: (#12) 소셜 로그인 기능 추가",
        "♻️ :: 서비스 계층 분리",  # 변형 선택자 포함
        "⚰️ :: 사용하지 않는 엔드포인트 삭제",
    ],
)
def test_valid_commit_messages(message: str) -> None:
    assert conv.check_commit_message(message) is None


@pytest.mark.parametrize(
    "message",
    [
        "fix: 버그 수정",  # 타입 없음
        "🐛 : 콜론 하나",  # 구분자 오류
        "🐛 :: Fix the header bug",  # 한국어 아님
        "🐛 :: " + "가" * 51,  # 50자 초과
        "🚀 :: 1.0",  # 릴리즈 형식 오류
        "🔀 :: 이슈 번호 없는 머지",  # merge 는 이슈 번호 필수
    ],
)
def test_invalid_commit_messages(message: str) -> None:
    assert conv.check_commit_message(message) is not None


def test_pr_title_requires_issue_number() -> None:
    assert conv.check_commit_message("⚡ :: 로그인 추가", is_pr_title=True) is not None
    assert conv.check_commit_message("⚡ :: (#3) 로그인 추가", is_pr_title=True) is None


@pytest.mark.parametrize(
    ("ref", "ok"),
    [
        ("feature/12_social-login", True),
        ("hotfix/7_token-401", True),
        ("develop", True),
        ("release/1.0.0", True),
        ("feature/social-login", False),  # 이슈 번호 없음
        ("fix/12_login", False),
    ],
)
def test_branch_names(ref: str, ok: bool) -> None:
    assert (conv.check_branch_name(ref) is None) is ok
