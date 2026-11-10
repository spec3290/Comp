"""로그에서 인가 코드·토큰을 가린다 (AGENTS.md 하드 규칙).

uvicorn 접속 로그는 쿼리 문자열을 그대로 남겨서 OAuth 콜백의 `code`·`state` 가 기록된다.
접속 로그 레코드의 인자에서 민감한 쿼리 값만 지운다.
"""

import logging
import re
from urllib.parse import unquote_plus

_SENSITIVE_NAMES = frozenset({"code", "state", "access_token", "refresh_token", "id_token"})
_QUERY_PARAM = re.compile(r"([?&])([^=&\s]*)=([^&\s]*)")
_ACCESS_LOGGER = "uvicorn.access"


def _mask(match: re.Match[str]) -> str:
    """민감한 쿼리 파라미터면 값을 *** 로 가린다."""
    # 접속 로그는 원본 쿼리를 남기므로 `%63ode=` 처럼 인코딩한 이름도 풀어서 확인한다
    if unquote_plus(match.group(2)).lower() not in _SENSITIVE_NAMES:
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}=***"


def redact(text: str) -> str:
    """문자열 안의 민감한 쿼리 파라미터 값을 가린다."""
    return _QUERY_PARAM.sub(_mask, text)


class RedactQueryFilter(logging.Filter):
    """접근 로그에서 토큰·OAuth 코드 같은 쿼리 값을 가리는 로그 필터."""

    def filter(self, record: logging.LogRecord) -> bool:
        """로그 메시지와 인자를 가린 뒤 항상 통과시킨다."""
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install_log_redaction() -> None:
    """uvicorn 접근 로거에 가림 필터를 한 번만 붙인다."""
    logger = logging.getLogger(_ACCESS_LOGGER)
    if not any(isinstance(f, RedactQueryFilter) for f in logger.filters):
        logger.addFilter(RedactQueryFilter())
