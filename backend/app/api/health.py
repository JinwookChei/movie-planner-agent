"""GET /api/health: 키 존재 여부(bool)만 돌려준다. (M8, F3019, NF3002)

화면은 이 값으로 "카카오 키가 없어 영화관 안내가 빠집니다" 같은 배너를 띄운다.
키 '값'은 어떤 형태로도(앞 몇 글자, 길이 포함) 보내지 않는다.
"""
from fastapi import APIRouter

from app import config
from app.schemas import Health

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=Health)
def health():
    return Health(
        keys={"gms": bool(config.GMS_API_KEY), "kobis": bool(config.KOBIS_API_KEY),
              "tmdb": bool(config.TMDB_API_KEY), "kakao": bool(config.KAKAO_REST_API_KEY)},
        log_fail_test=config.LOG_FAIL,
    )
