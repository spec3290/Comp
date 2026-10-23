---
name: feature-implementation
description: docs/features.md의 기능(F1-6 등)을 구현하거나 수정할 때 사용한다. 범위 확인부터 레이어별 구현, 테스트, 명세 상태 갱신, PR까지의 절차를 따른다.
---

# 기능 구현

규칙의 근거는 [AGENTS.md](../../../AGENTS.md), 동작의 근거는 [docs/features.md](../../../docs/features.md), 결정 사항은 [docs/decisions.md](../../../docs/decisions.md)다. 여기에는 절차만 둔다.

## 절차

1. **범위 확인** — 구현할 기능 ID의 수용 기준을 features.md에서 읽는다. 1차(MVP) 밖이면 중단하고 사용자에게 확인한다.
2. **결정 확인** — decisions.md의 `결정이 필요한 항목`에서 이 기능에 걸린 Q-번호가 `열림`인지 본다. 열려 있으면 제안 기본값으로 가도 되는지 사용자에게 묻고, 답을 받으면 decisions.md에 반영한다. 추측으로 구현하지 않는다.
3. **이슈·브랜치** — `git-workflow` 스킬로 이슈를 만들고 `feature/{번호}_{todo}` 브랜치를 만든다.
4. **테스트 먼저** — 수용 기준 항목마다 테스트를 쓴다. 이름은 동작으로 쓴다 (`test_closed_contest_click_returns_409`). `@pytest.mark.feature("F2-5")`를 단다. 시간 의존 테스트는 기준 날짜를 인자로 넘겨 고정하고 경계값(마감 당일, 시작 전날)을 넣는다. 테스트는 실제 네트워크를 호출하지 않는다.
5. **안쪽부터 구현** — 아래 순서로 만든다.
6. **로컬 사전 점검(권장)** — `uv run ruff format . && uv run ruff check .`, `uv run mypy`, `uv run lint-imports`, `uv run pytest`. 통과 기준은 CI다.
7. **명세 갱신** — features.md의 해당 기능 상태를 `wip`/`done`으로 바꾼다. 테스트가 있는 기능만 `done`. API·데이터 모델이 바뀌면 decisions.md의 ADR-0004/0005를 같은 변경에서 고친다.
8. **커밋·PR** — `git-workflow` 스킬 규칙으로 커밋하고 PR을 올린다. CI가 통과한 뒤 `code-review` 스킬로 리뷰를 요청한다.

## 레이어별 구현 순서 (ADR-0007, AC-1~3 준수)

기능 패키지(`app/contests`, `app/auth`) 안에서 아래 순서로 만든다.

| 순서 | 위치 | 할 일 |
|---|---|---|
| 1 | `rules.py` | 순수 규칙(상태 계산, 병합 키, 분류 규칙 적용)과 도메인 예외. 프레임워크·DB·HTTP import 금지 (AC-1) |
| 2 | `crawlers/`, `oauth.py` | 구현체가 여러 개인 외부 경계만 Protocol(`ContestSource`, `OAuthProvider`)과 구현체로 둔다 |
| 3 | `repository.py`, `models.py` | DB 접근. PostgreSQL은 SQLAlchemy 모델, MongoDB는 컬렉션 (ADR-0008) |
| 4 | `service.py` | 유스케이스 하나를 한 흐름으로. repository와 rules를 호출하고 도메인 예외를 던진다 |
| 5 | `router.py` | 변환과 service 호출만. repository를 직접 import하지 않는다 (AC-2) |
| - | `app/core/` | 설정·시간·DB 연결 등 공통. 기능 패키지를 import하지 않는다 (AC-3) |

## 기능별 주의

- **인증(F1)**: 만료·위조·블랙리스트·회전된 Refresh는 401, 구분은 `error.code`. 토큰·OAuth 코드는 로그에 남기지 않는다.
- **수집(F2-1/2)**: 소스 하나의 실패가 다른 소스를 막지 않는다. 실패 시 기존 데이터 유지 + `crawl_runs` 기록. upsert는 멱등.
- **분류(F3)**: 키워드 규칙은 코드 분기가 아니라 데이터. 매칭이 없으면 `미분류`이고 필터에서 제외하지 않는다.
- **목록(F2-3)**: 접수 상태는 날짜로 계산한다. 마감 대회는 기본 목록에서 제외.
