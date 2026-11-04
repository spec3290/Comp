"""테스트 전체 설정.

테스트는 로컬 `.env` 를 읽지 않는다. 읽으면 로컬에서만 통과하고 CI(.env 없음)에서 실패하는
차이가 생기고, 테스트가 실제 비밀 값(JWT_SECRET, Google 시크릿)을 쓰게 된다.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import Settings
from app.core.db import Document
from app.core.time import KST

Settings.model_config["env_file"] = None

MONGODB_TEST_URI = os.environ.get("MONGODB_TEST_URI")


@pytest.fixture
def mongo_db() -> Iterator[Database[Document]]:
    """실제 MongoDB 의 임시 DB. 로컬에서는 MONGODB_TEST_URI 가 없으면 건너뛴다.

    CI 에서는 건너뛰지 않고 실패시킨다. 설정이 빠져 통합 테스트가 조용히 빠지는 것을 막는다.
    """
    if not MONGODB_TEST_URI:
        if os.environ.get("CI"):
            pytest.fail("CI 에서는 MONGODB_TEST_URI 가 필요하다 (.github/workflows/ci.yml)")
        pytest.skip("MONGODB_TEST_URI 미설정")
    client: MongoClient[Document] = MongoClient(MONGODB_TEST_URI, tz_aware=True, tzinfo=KST)
    database = client[f"test_{uuid.uuid4().hex[:12]}"]
    try:
        yield database
    finally:
        # Atlas 사용자에게 dropDatabase 권한이 없을 수 있어 컬렉션을 지운다 (빈 DB 는 사라진다)
        for collection in database.list_collection_names():
            database.drop_collection(collection)
        client.close()
