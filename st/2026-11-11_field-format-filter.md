# 코드 리뷰: 자격 필터 제거, 분야·형식 필터 추가

- 날짜: 2026-11-11
- 브랜치: `feature/37_field-format-filter` (이슈 #37)
- 기능: F3-1(분야·형식 분류), F3-3(분야·형식 필터), 관련 F2-1·F2-3·F2-4 수용 기준 문구
- 결정: Q-23 신설(사용자 결정 2026-11-11), Q-8·Q-19 폐기, Q-9 문구 갱신, ADR-0004·0005·0009 갱신

## 배경
2026-11-10 실데이터 388건(Dev Event 25, DACON 282, Devpost 81) 중 참가 자격 원문이 있는 대회는 0건이었다. DACON·Devpost 크롤러는 원래 빈 문자열을 넣고, Dev Event `description`은 모두 비어 있다. 자격 태그는 제목에 걸린 4건에만 붙어 자격 필터가 사실상 동작하지 않았다.

## 리뷰 결과: approve

### 반드시 수정 (blocker)
- 없음

### 권장
1. **응답 `field`와 쿼리 `field`의 뜻이 다르다** — 응답 `field`는 소스 태그 원문(예: `"Machine Learning/AI, Web"`)이고, 쿼리 `field`는 분야 어휘(`AI` 등)로 거른다. 프론트 호환을 위해 응답 필드 이름은 그대로 두고 ADR-0004에 차이를 적었다. 프론트가 정리되면 응답 쪽을 `source_tags`로 바꾸는 것을 검토한다.
2. **기존 MongoDB 문서** — 배포 직후에는 기존 문서의 `tags`가 `{education, team_type, region}` 형태라 다음 정기 수집(매일 06:00 KST) 전까지 전부 미분류(`tag_check_needed: true`)로 보인다. 머지 후 `collect.yml`을 수동 실행(`workflow_dispatch`)하면 바로 다시 분류된다. 대표 소스가 아닌 출처만 남은 문서나 이미 지워질 문서에 남은 `eligibility_text`는 읽지 않으므로 동작에 영향이 없다.
3. **분류 범위** — 2026-11-11 실데이터(저장 대상만): Devpost 81건은 분야·형식 모두 분류, Dev Event 15건 중 분야 11건·형식 7건. 남은 Dev Event는 `기술일반` 태그나 '챌린지'·'대회' 제목이라 의도대로 미분류다. 새 소스 태그가 생기면 `classification_rules.toml`에 대응을 더한다.
4. **`pyproject.toml` 미커밋 변경** — 작업 전부터 있던 `[tool.setuptools.packages.find]` 추가분은 이번 작업과 무관해 커밋하지 않았다 (이전 리뷰 권장 3과 같음).

### 확인한 것
- 명세: F3-1 수용 기준(소스 태그 대응표·제목 단어·소스 기본값·미분류 유지·규칙은 데이터) 구현, 상태 `done`. F3-3은 `field`·`format` 다중 선택과 미분류 노출까지 구현, 프로필 관심 분야를 기본값으로 쓰는 부분이 남아 `wip` 유지. F2-1·F2-4의 참가 자격 원문 항목은 Q-23 근거로 뺐다.
- 결정: 열린 Q-항목 추측 없음. API·데이터 모델 변경을 ADR-0004(쿼리·응답 필드), ADR-0005(contests 컬럼, contest_tags, classification_rules), ADR-0009(차원·파일명)에 같은 변경으로 반영.
- 아키텍처: 분류는 `rules.py`의 순수 함수(`classify`), 서비스가 수집 때 호출, 저장소는 `tags.{field,format}` 쿼리만 만든다. `rules.py`가 `app.auth`를 import하지 않도록 분야 enum은 따로 두고, 프로필 관심 분야와 값이 같은지는 테스트로 묶었다. AC-1~3 유지.
- 정확성: 소스 태그는 통째 일치(공백·대소문자 무시)라 'AI 헬스케어' 같은 부분 일치로 태그가 붙지 않는다. 형식 소스 기본값은 제목에 형식 단어가 없을 때만 쓴다. 어휘 밖 값·빈 키워드는 규칙 로딩 때 `ValueError`, 쿼리는 400.
- 보안: 입력은 enum 검사 후 `$in`에만 쓴다. 새 정규식 조립 없음.
- 주석: 새로 쓰거나 고친 모듈·클래스·함수 모두 한 줄 docstring.
- 테스트: `test_classification.py`(분류 9 + 확인 필요 2), API·필터·수집·저장소 테스트를 새 차원으로 바꿈. 규칙 파일의 소스 키가 실제 크롤러 이름인지, 분야 어휘가 프로필 관심 분야와 같은지 검사. 네트워크 호출 없음.
- Git 규약: 이슈 #37, `feature/37_field-format-filter`, 커밋 `⚡`/`📝`.

### 실행한 검사
| 검사 | 결과 |
|---|---|
| `uv run ruff format --check .` | 통과 (104 files already formatted) |
| `uv run ruff check .` | 통과 |
| `uv run python -m mypy` | 통과 (58 source files) |
| `lint-imports` (`importlinter.cli`를 python으로 실행, exe는 Windows 애플리케이션 제어 정책이 막음) | 통과 (3 kept, 0 broken) |
| `uv run python -m pytest -q` | 231 passed, 16 skipped (`MONGODB_TEST_URI` 미설정으로 Mongo 통합 테스트 건너뜀, CI에서 실행) |
