"""로그 민감 값 가림 (F1-6). uvicorn 접속 로그 레코드와 같은 모양으로 확인한다."""

import logging

import pytest

from app.core.log_redaction import RedactQueryFilter, install_log_redaction, redact

CALLBACK = (
    "/auth/google/callback?state=abc123&iss=https://accounts.google.com&code=4/0Ab_xyz&scope=openid"
)


def _access_record(path: str) -> logging.LogRecord:
    # uvicorn: '%s - "%s %s HTTP/%s" %d' % (client, method, path, http_version, status)
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", 200),
        None,
    )


@pytest.mark.feature("F1-6")
def test_access_log_hides_oauth_code_and_state() -> None:
    record = _access_record(CALLBACK)

    RedactQueryFilter().filter(record)

    message = record.getMessage()
    assert "4/0Ab_xyz" not in message
    assert "abc123" not in message
    assert "code=***" in message and "state=***" in message
    assert "scope=openid" in message  # 민감하지 않은 값은 남긴다


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/x?access_token=eyJ.a.b", "/x?access_token=***"),
        ("/x?refresh_token=r&id_token=i", "/x?refresh_token=***&id_token=***"),
        ("/contests?q=zipcode=1", "/contests?q=zipcode=1"),  # 단어 경계: zipcode 는 그대로
        ("/contests?page=1", "/contests?page=1"),
    ],
)
def test_redact_masks_only_sensitive_query_values(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.feature("F1-6")
def test_install_adds_filter_once() -> None:
    install_log_redaction()
    install_log_redaction()

    filters = logging.getLogger("uvicorn.access").filters
    assert sum(isinstance(f, RedactQueryFilter) for f in filters) == 1
