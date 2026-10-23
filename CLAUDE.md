# CLAUDE.md — 대회 수집 서비스 (Contest Collector)

이 파일은 **지도이자 조정자**다. 상세 규칙은 여기에 쓰지 않고 아래 하네스 구성요소로 보낸다.
120줄을 넘기지 않는다 (`make docs-check`가 검사).

## 프로젝트

여러 사이트에 흩어진 대회를 수집·중복 제거·자격 분류해 보여 주는 백엔드.
스택: Python 3.13+, uv, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL, PyJWT, httpx, pytest.
현재 범위는 **1차 MVP** (F1·F2·F3 중 13개 기능). 2차 이후는 [로드맵](docs/product/roadmap.md)에만 있다.

## 하네스 구성과 역할 분담

| 구성요소 | 위치 | 담당 | 강제 수단 |
|---|---|---|---|
| 에이전트 지침 | 이 파일 | 라우팅, 우선순위, 작업 절차 | 길이·링크 검사 |
| 아키텍처 제약 | [constraints.md](docs/architecture/constraints.md), `pyproject.toml` | 레이어, 의존 방향, 금지 API | import-linter, ruff, mypy |
| 피드백 루프 | [feedback-loop.md](docs/engineering/feedback-loop.md), `Makefile`, `.claude/`, CI | 변경 직후 검증과 완료 게이트 | hooks, `make check`, CI |
| 지식 저장소 | [docs/](docs/README.md) | 명세, 결정([decisions.md](docs/architecture/decisions.md)), 학습 기록 | `scripts/check_docs.py` |

## 조정 규칙 (충돌이 나면 이 순서가 이긴다)

1. **자동 검사 결과**(린터·타입·아키텍처·테스트)가 문서보다 우선한다. 검사가 틀렸다면 [decisions.md](docs/architecture/decisions.md)에 결정을 기록하고 바꾼다.
2. **제품 동작**은 [features.md](docs/product/features.md)의 수용 기준이 단일 출처다. 코드와 다르면 같은 변경에서 맞춘다.
3. **구조 규칙**은 constraints.md와 `pyproject.toml`이 단일 출처다. 이 파일에 복사하지 않는다.
4. **제약을 약화하지 않는다.** 검사를 통과시키려고 린트 설정·계약·테스트를 완화·삭제하지 않는다.
   정말 필요하면 decisions.md에 ADR을 먼저 쓰고 사용자 승인을 받는다.
5. **모호하면 추측하지 않는다.** [open-questions.md](docs/product/open-questions.md) 표에 적고 사용자에게 묻는다.
   질문은 decisions.md의 결정 묶음(`P-n`)에 연결하고, 결정되면 decisions.md와 해당 문서에 반영한 뒤 상태를 `결정됨`으로 바꾼다.
6. **같은 실수가 2번 나오면 자동화한다.** [learnings.md](docs/engineering/learnings.md)에 기록하고
   린트 규칙 > 아키텍처 계약 > 테스트 > docs 규칙 순으로 올린다. 이 파일에 줄을 추가하는 것은 마지막 수단이다.

## 작업 절차

1. **범위 확인** — 작업할 기능 ID(예: F1-4)를 찾고 features.md의 수용 기준을 읽는다. MVP 밖이면 중단하고 확인한다.
2. **맥락 로드** — [docs/README.md](docs/README.md) 지도에서 관련 문서만 읽는다 (architecture, decisions, data-model, api-conventions).
3. **계획** — 파일 3개 이상 또는 새 테이블이 걸리면 [exec-plan 템플릿](docs/engineering/exec-plans/_template.md)으로 계획부터 쓴다.
4. **구현** — 테스트를 먼저(또는 함께) 쓰고 `@pytest.mark.feature("F?-?")`로 기능 ID를 단다.
5. **검증** — 수정 직후 훅이 돌고, 완료 선언 전 `make check`를 통과한다. 실패 로그를 읽고 원인을 고친다.
6. **환류** — features.md 상태(todo/wip/done) 갱신, 구조가 바뀌면 docs 수정, 결정이 났으면 decisions.md·open-questions.md 갱신, 배운 점은 learnings.md.

## 명령

- `make check-fast` 린트 + 아키텍처 + docs (수 초) / `make check` 전체 (완료 게이트)
- `make fmt` 자동 정리 / `make test` / `make arch` / `make docs-check` / `make run`

## 하드 규칙 (예외 없음)

- 시간은 KST 기준의 timezone-aware `datetime`만 쓴다 (naive 금지, ruff DTZ가 검사). 대회 상태는 저장하지 않고 날짜로 계산한다.
- 비밀 값은 환경변수로만. `.env`를 읽거나 커밋하지 않는다. 로그에 토큰·개인정보를 남기지 않는다.
- 에러 응답은 [api-conventions.md](docs/architecture/api-conventions.md)의 단일 포맷. 만료·위조 토큰은 항상 401.
- 크롤링 실패는 기존 데이터를 건드리지 않고 로그만 남긴다 (F2-1).
- 외부 HTTP는 httpx만, 외부 시스템은 `app/domain/ports.py`의 Protocol 뒤에 둔다.

## 막혔을 때

같은 실패가 3번 반복되면 멈춘다. 시도한 것과 실패 로그를 learnings.md에 적고 사용자에게 보고한다.
