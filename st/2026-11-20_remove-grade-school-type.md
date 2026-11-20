# 코드 리뷰: 프로필 학년·학교 유형 삭제

- 날짜: 2026-11-20
- 브랜치: `feature/68_remove-grade-school-type` (이슈 #68)
- 기능: F1-7(프로필 설정), F3-4(맞춤 추천, 확장 지점 문구만)
- 결정: Q-38 추가(2026-11-20 사용자 결정), Q-5의 학년·학교 유형 부분 삭제 표시

## 배경
사용자 질문: "user 테이블에서 grade랑 스쿨타입 필요할까". `grade`·`school_type`은 `/users/me` 응답과 프로필 저장에서만 쓰이고 `app/contests`·`app/notifications` 어디에서도 읽지 않았다. 참가 자격(학력) 분류는 Q-23에서 없앴고 F3-4 추천도 분야 태그만 맞출 수 있어, 쓰지 않는 개인정보를 받는 상태였다. 사용자가 삭제로 결정했다.

## 바뀐 것
- `app/auth/rules.py`: `SchoolType`, `GRADE_MIN/MAX` 삭제, `ProfileInput`에서 두 필드 삭제. 모듈 docstring 갱신.
- `app/auth/schemas.py`: `ProfileIn`·`UserOut`에서 두 필드 삭제. Pydantic 기본(`extra="ignore"`)이라 예전 프론트가 보내도 400이 아니라 무시된다.
- `app/auth/service.py`, `repository.py`, `models.py`: 저장 경로와 컬럼 매핑 삭제.
- `migrations/versions/0006_drop_grade_school_type.py`: `users.grade`·`school_type` 컬럼 삭제. downgrade는 빈 컬럼만 되살린다(지운 값은 복구되지 않음, docstring에 명시).
- 테스트: `test_grade_is_optional` → `test_grade_and_school_type_are_ignored`(보내도 200, 응답에 없음). 400 케이스에서 학년·학교 유형 항목 삭제. `test_withdraw.py`, `test_contest_api.py`의 픽스처 정리.
- 문서: decisions.md API 규약(`/users/me`, `PUT /users/me/profile`), users 테이블 정의, 인덱스 메모, Q-5, Q-38 추가. features.md F1-7 입력과 F3-4 확장 지점.

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
- API 응답에서 필드가 빠지므로 프론트가 `grade`·`school_type`을 읽거나 온보딩 폼에 두고 있다면 같이 지워야 한다. 요청 쪽은 무시되므로 배포 순서는 상관없다.
- 운영 DB에서 `0006`을 돌리면 기존 값은 사라진다. 되돌릴 일이 있으면 배포 전에 백업을 떠 둔다.

### 확인한 것
- 명세: F1-7 입력이 닉네임·관심 분야로 바뀌었고, 관심 분야 필터(F3-3, Q-24)는 손대지 않았다.
- 결정: Q-38로 결정을 남기고 Q-5의 해당 부분을 삭제 표시했다. ADR-0004(API)·ADR-0005(데이터 모델) 표를 같은 변경에서 고쳤다.
- 아키텍처: auth 패키지 안 삭제뿐. 새 import 없음, 계약 영향 없음.
- 보안: 수집하는 개인정보가 줄었다. 로그 출력 변화 없음.
- 주석: 새 마이그레이션 모듈·함수와 바꾼 테스트에 docstring.
- 테스트: `@pytest.mark.feature("F1-7")` 유지. `grep -rn "SchoolType|GRADE_|school_type|grade" app tests`로 남은 참조가 새 테스트뿐임을 확인.
- Git 규약: 이슈 #68, `feature/68_remove-grade-school-type`, 커밋 `⚰️`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `python3 -m py_compile` (바뀐 파일) | 통과. 로컬에 `uv`가 없어 나머지는 CI에서 확인 |
| `uv run ruff format --check .` / `uv run ruff check .` | CI에서 확인 |
| `uv run mypy` | CI에서 확인 |
| `uv run lint-imports` | CI에서 확인 |
| `uv run pytest` | CI에서 확인 |
| `python scripts/check_conventions.py` | CI에서 확인 |
