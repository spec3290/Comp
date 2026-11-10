"""헬스 체크 엔드포인트."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """서버가 살아 있는지 알려 준다."""
    return {"status": "ok"}
