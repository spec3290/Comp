# 코드 리뷰: 알림 발급 (F4-1)

- 날짜: 2026-11-16
- 브랜치: `feature/58_notifications` (이슈 #58). `feature/56_unknown-deadline`(PR #57) 위에서 작업했다. 마감일 미확인 대회는 마감 알림을 보내지 않는다는 규칙(Q-35)이 필요해서다.
- 기능: F4-1(알림 발급). 2차 F4-3(알림 목록·읽음 처리)을 합쳤다.
- 결정: Q-34 결정됨(서비스 안 알림 목록, 사용자 결정). Q-36 신설(발표일 제외, 종류별 켜기·끄기, CLI 실행, 사용자 결정 + 구현 가정)

## 배경
사용자 결정(2026-11-16):
- 채널은 서비스 안 알림 목록이다.
- 목록 조회와 읽음 처리까지 F4-1에 넣는다.
- 발표일은 수집 필드가 없어 빼고 시작일만 쓴다.
- 시점 설정은 정해진 항목을 켜고 끄는 방식이다.
- 실행은 CLI 명령만 만든다. 운영 PostgreSQL이 아직 없어서 스케줄은 나중에 붙인다.

## 바뀐 것
- 새 기능 패키지 `app/notifications/`
  - `rules.py`: 알림 종류 `NotificationKind`(D-7·D-3·D-1·시작 전날)를 정의한다. `due_notifications`는 오늘 보낼 알림을 계산하는데, 보낼 날이 정확히 오늘인 것만 고르고 마감일 미확인 대회의 마감 알림은 뺀다. `message`는 보여 줄 문장을 만든다. 순수 파이썬이다(AC-1).
  - `models.py`: `notifications`(`UNIQUE(user_id, contest_id, kind)`, 보낸 때의 제목·날짜)와 `notification_opt_outs`(끈 종류만 행으로 둔다) 테이블. 둘 다 users FK `ON DELETE CASCADE`라 탈퇴하면 같이 지워진다.
  - `repository.py`: 모든 북마크 조회, 끈 종류 조회, 중복 확인 후 추가, 목록(전체·안 읽은 건수 포함), 읽음 처리(처음 읽은 시각 유지), 모두 읽음, 설정 교체.
  - `service.py`: `NotificationDispatcher.dispatch`는 북마크와 최신 대회 날짜로 오늘 알림을 만든다. 저장을 해제했거나 삭제된 대회는 계산 대상에 없어서 보내지 않는다. `NotificationService`는 목록·읽음·설정을 맡는다.
  - `router.py`: `GET /users/me/notifications`, `POST .../{id}/read`, `POST .../read-all`, `GET`·`PUT /users/me/notification-settings`.
  - `dispatch.py`: CLI `uv run python -m app.notifications.dispatch`.
- 마이그레이션 `0005`, `migrations/env.py` 모델 등록, `app/main.py` 라우터 등록.
- AC-3 계약에 `app.notifications`를 더했다(제약 강화).
- 문서: features.md(F4-1 done, F4-3은 F4-1에 합침), decisions.md(Q-34·Q-36, ADR-0004 API, ADR-0005 테이블), AGENTS.md(구조도·AC-3).

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음. (실행 중 발견한 버그 1건은 이 변경 안에서 고쳤다. 아래 '실제 실행' 참고.)

### 권장
- **발급은 북마크마다 중복 확인 쿼리를 한 번씩 보낸다(N+1).** 지금 규모에서는 문제없다. 북마크가 많아지면 이미 보낸 키를 한 번에 읽도록 바꾼다.
- **발급을 동시에 두 번 돌리면 유일 제약이 커밋을 막는다.** 그러면 그 실행 전체가 롤백되고 1로 끝난다. 다음 실행이 같은 날이면 다시 만든다. 스케줄을 붙일 때 수집 워크플로의 `concurrency`를 같이 쓰면 이 경우가 생기지 않는다.
- **실행을 건너뛴 날의 알림은 보내지 않는다(Q-36 가정).** 늦게 보내면 'D-7' 문장이 틀리기 때문이다. 운영 스케줄이 실패하는 날이 잦으면 다시 검토한다.
- **접수가 끝난 대회도 시작 전날 알림은 보낸다.** 저장한 대회의 주요 일정이라서다.
- `Bookmark`(contests)도 users FK를 쓰지만 `app.auth.models`를 직접 import하지 않는다. 지금은 앱과 이 CLI 모두 회원 모델을 먼저 올려서 문제가 없다. 다른 CLI가 생기면 같은 버그가 날 수 있다.
- **배포 전 남은 일(Q-36):** 운영 PostgreSQL을 정하고, `collect.yml`에서 수집 다음 단계로 `python -m app.notifications.dispatch`를 붙인다(`DATABASE_URL` 시크릿 필요).

### 확인한 것
- 명세: F4-1 수용 기준을 모두 구현했다. D-7·D-3·D-1과 시작 전날, 종류별 켜기·끄기, 최신 마감일로 계산, 저장 해제·삭제 시 미발송, 한 번만 발송, 마감일 미확인 제외, 목록·읽음 처리. features.md 상태를 고쳤다.
- 결정: 열린 Q를 추측으로 구현하지 않았다. Q-34·Q-36을 사용자 답으로 닫았고, 구현 가정은 Q-36에 적었다. ADR-0004·0005를 같이 고쳤다.
- 아키텍처: 판정 규칙은 `rules.py`, DB는 repository, 유스케이스는 service에 두었다. router는 service만 쓴다. AC-1~3 유지(AC-3은 강화).
- 정확성: KST 날짜로 계산한다(`now_kst().date()`). 경계값을 테스트했다(D-8·D-6·D-2·마감 당일·지난 뒤는 알림 없음, 시작 당일은 알림 없음). naive datetime 없음.
- 보안: 남의 알림 읽음 처리는 404로 존재를 숨긴다. 모든 엔드포인트가 Access 토큰을 요구한다(401 테스트). 로그에는 건수·날짜만 남긴다.
- 주석: 새로 쓰거나 고친 모듈·클래스·함수·테스트에 한 줄 docstring을 달았다.
- 테스트: `tests/unit/test_notifications.py` 28건.
  - 규칙 12(파라미터 포함)
  - 발급 6
  - 목록·읽음 API 5
  - 설정 API 3
  - 401 1
  - CLI 외래 키 회귀 1
  - 그 밖에 `test_withdraw.py`가 탈퇴 시 알림·설정 삭제를 확인한다.
- Git 규약: 이슈 #58, `feature/58_notifications`, 커밋 `⚡`.

### 실제 실행 (로컬 도커 PostgreSQL + Atlas 대회 읽기 전용)
- `alembic upgrade head`로 0003 → 0005를 적용했고, `downgrade 0004` 후 다시 올렸다.
- 오늘(11/16) 알림 대상 대회 5건을 Atlas에서 찾았다. 회원 5에게 D-1·D-3 대회 두 개를, 회원 6에게 D-1 대회를 저장하고 회원 6은 D-1을 껐다.
- **첫 실행에서 버그를 찾았다.** CLI가 앱을 거치지 않아 `users` 모델이 등록되지 않았고, `NoReferencedTableError`가 났다. `app/notifications/models.py`가 `app.auth.models`를 import하도록 고쳤다. 새 프로세스에서 확인하는 회귀 테스트를 추가했고, 수정 전 코드에서 실패하는 것도 확인했다.
- 고친 뒤 결과:
  - 첫 실행은 2건을 만들었다(회원 5의 D-1·D-3). 회원 6은 D-1을 꺼서 0건이다.
  - 두 번째 실행은 0건이었다(중복 없음).
  - PostgreSQL에서 목록(전체 2·안 읽음 2), 읽음 처리(안 읽음 1), 설정 조회도 확인했다.
- 확인 뒤 임시 북마크·알림·설정 행을 지웠다.
- 참고: `.env`의 `localhost:5434`는 Windows에서 IPv6로 먼저 연결해 멈췄다. 이번 확인에서는 `127.0.0.1`로 덮어써서 실행했다.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` / `uv run ruff check .` | 통과 |
| `uv run mypy` | 통과 (74 files) |
| `uv run lint-imports` | 3 kept, 0 broken |
| `uv run pytest` | 343 passed, 32 skipped (Mongo 통합 테스트는 로컬에 `MONGODB_TEST_URI`가 없어 건너뜀, CI에서 돈다) |
| `python scripts/check_conventions.py` | 실패: 이 브랜치가 올라탄 #57의 기존 커밋 `chore: setuptools 패키지 탐색을 app으로 한정`이 규약 형식이 아니다(#57 CI도 같은 이유로 실패). 이 작업의 커밋은 규약을 따른다 |
