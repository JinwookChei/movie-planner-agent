"""환경변수와 상수를 한곳에 모은다.

검증할 때 상수만 바꿔 상황을 재현할 수 있다. 예: MAX_REPLAN=1로 상한 도달 확인.
키 값은 이 모듈 밖으로 출력하지 않는다 (NF3001, NF3002).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent  # .../backend
ROOT_DIR = BACKEND_DIR.parent                         # 프로젝트 루트

# .env는 루트에 있다. backend/에서 실행해도 찾을 수 있게 경로를 직접 준다.
load_dotenv(ROOT_DIR / ".env")

# ── API 키 ──
GMS_API_KEY = os.getenv("GMS_API_KEY", "")
KOBIS_API_KEY = os.getenv("KOBIS_API_KEY", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")

# ── LLM (SSAFY GMS) ──
MODEL = "gpt-5.4-nano"
GMS_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"
LLM_TIMEOUT = 30

# ── 외부 API ──
KOBIS_BASE_URL = "http://www.kobis.or.kr/kobisopenapi/webservice/rest"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
HTTP_TIMEOUT = 10

# ── Agent 규칙 (기획서 4절) ──
AD_BUFFER = 20        # 광고·입장 시간(분). need = show_tm + AD_BUFFER
MAX_REPLAN = 3        # 재계획 상한 (NF3004)
BOX_TOP_N = 5         # 처음 볼 박스오피스 순위 수. 재계획 1에서 10으로 늘린다
NEW_RELEASE_MAX = 3   # TMDB 신작 보충 최대 편수

# ── 저장소 ──
DB_PATH = BACKEND_DIR / "data" / "app.db"

# ── CORS (프론트 정적 서버) ──
CORS_ORIGINS = ["http://localhost:5500", "http://127.0.0.1:5500"]
