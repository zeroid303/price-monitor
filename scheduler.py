"""
가격 모니터링 파이프라인 오케스트레이터.

카테고리별 흐름:
  1. past 로테이션: *_raw_now → *_raw_past, *_normalize_now → *_normalize_past
  2. 크롤러 실행 → *_raw_now.json 생성
  3. normalize 적용 → *_normalize_now.json 생성
  4. 변동 감지: *_normalize_past vs *_normalize_now diff

사용:
  python scheduler.py card        # card 파이프라인 실행
  python scheduler.py             # 기본 card
"""
import json
import logging
import os
import shutil
import sys
from importlib import import_module

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CONFIG_DIR = os.path.join(BASE_DIR, "config")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("scheduler")


# ── 카테고리별 설정 ──
# crawlers: (company_id, module_path, crawl_fn, save_fn)
CATEGORIES = {
    "card": {
        "rule_path": os.path.join(CONFIG_DIR, "card_mapping_rule.json"),
        "crawlers": [
            ("printcity", "crawlers.PrintcityCardCrawler", "crawl_all", "save"),
            ("bizhows",   "crawlers.BizhowsCardCrawler",   "crawl_all", "save"),
            ("swadpia",   "crawlers.SwadpiaCardCrawler",   "crawl_all", "save"),
            ("wowpress",  "crawlers.WowpressCardCrawler",  "crawl_all", "save"),
            ("dtpia",     "crawlers.DtpiaCardCrawler",     "crawl_all", "save"),
        ],
    },
    "sticker": {
        "rule_path": os.path.join(CONFIG_DIR, "sticker_mapping_rule.json"),
        "crawlers": [
            ("printcity", "crawlers.PrintcityStickerCrawler", "crawl_all", "save"),
            ("bizhows",   "crawlers.BizhowsStickerCrawler",   "crawl_all", "save"),
            ("swadpia",   "crawlers.SwadpiaStickerCrawler",   "crawl_all", "save"),
            ("dtpia",     "crawlers.DtpiaStickerCrawler",     "crawl_all", "save"),
            ("wowpress",  "crawlers.WowpressStickerCrawler",  "crawl_all", "save"),
        ],
    },
    # TODO: envelope, flyer, postcard — 각 크롤러 신규 스키마로 마이그레이션 후 추가
}


# ── 파일 경로 헬퍼 ──
def _path(company: str, category: str, kind: str, when: str) -> str:
    """kind: raw|normalize, when: now|past"""
    return os.path.join(OUTPUT_DIR, f"{company}_{category}_{kind}_{when}.json")


# ── 로테이션 ──
def rotate_now_to_past(company: str, category: str, kind: str):
    """*_{kind}_now.json 존재하면 *_{kind}_past.json으로 덮어쓰기."""
    now_path = _path(company, category, kind, "now")
    past_path = _path(company, category, kind, "past")
    if os.path.exists(now_path):
        shutil.copy2(now_path, past_path)


# ── 정규화 ──
def normalize_file(company: str, category: str, rule_path: str):
    """{company}_{category}_raw_now.json 읽어 normalize 적용 → _normalize_now.json 저장."""
    from common.normalize import load_rule, normalize_output

    raw_path = _path(company, category, "raw", "now")
    norm_path = _path(company, category, "normalize", "now")

    if not os.path.exists(raw_path):
        logger.warning(f"  raw 파일 없음: {raw_path}")
        return

    with open(raw_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    rule = load_rule(rule_path)
    normalized = normalize_output(raw, rule)
    with open(norm_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    logger.info(f"  정규화 완료: {len(normalized.get('items', []))}건 → {os.path.basename(norm_path)}")


# ── 카테고리 실행 ──
def run_category(category: str) -> list[dict]:
    if category not in CATEGORIES:
        logger.error(f"지원하지 않는 카테고리: {category}. 지원: {list(CATEGORIES)}")
        return []

    cat = CATEGORIES[category]
    rule_path = cat["rule_path"]
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    logger.info("=" * 60)
    logger.info(f"[{category}] 파이프라인 시작")
    logger.info("=" * 60)

    for company, module_path, crawl_fn_name, save_fn_name in cat["crawlers"]:
        logger.info(f"\n▶ {company}")

        # 1. 로테이션 (raw, normalize 양쪽)
        rotate_now_to_past(company, category, "raw")
        rotate_now_to_past(company, category, "normalize")

        # 2. 크롤링 → raw_now.json
        try:
            mod = import_module(module_path)
            items = getattr(mod, crawl_fn_name)()
            getattr(mod, save_fn_name)(items)
        except Exception as e:
            logger.error(f"  크롤링 실패 ({company}): {e}")
            continue

        # 3. 정규화 → normalize_now.json
        try:
            normalize_file(company, category, rule_path)
        except Exception as e:
            logger.error(f"  정규화 실패 ({company}): {e}")
            continue

    logger.info(f"\n[{category}] 완료. 변동 감지는 대시보드가 past/now 비교로 수행.")


if __name__ == "__main__":
    cat = sys.argv[1] if len(sys.argv) > 1 else "card"
    run_category(cat)
