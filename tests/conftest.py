"""테스트 전체 설정.

테스트는 로컬 `.env` 를 읽지 않는다. 읽으면 로컬에서만 통과하고 CI(.env 없음)에서 실패하는
차이가 생기고, 테스트가 실제 비밀 값(JWT_SECRET, Google 시크릿)을 쓰게 된다.
"""

from app.core.config import Settings

Settings.model_config["env_file"] = None
