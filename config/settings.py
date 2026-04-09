"""프로젝트 설정"""
import os

# 출력 디렉토리
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")

# 이전 크롤링 결과 저장 (변동 감지용)
HISTORY_FILE = os.path.join(OUTPUT_DIR, "price_history.json")

# ── 가격 변동 감지 임계값 ─────────────────────────────────
# 가격이 이 비율(%) 이상 변동하면 알림
PRICE_CHANGE_THRESHOLD = 5.0   # 5% 이상 변동 시 알림

# ── 스케줄 설정 ───────────────────────────────────────────
SCHEDULE_DAY = "wednesday"
SCHEDULE_TIME = "10:00"
