"""
가격 모니터링 v3
- 5개 제품 카테고리 병렬 크롤링: 명함, 스티커, 봉투, 전단지, 엽서
- 각 카테고리별 reference.json 기반 매칭 + 히스토리 비교

사용법:
  python scheduler.py              # 전체 실행
  python scheduler.py card         # 명함만 실행
  python scheduler.py sticker      # 스티커만 실행
  python scheduler.py envelope     # 봉투만 실행
"""
import sys
import os
import re
import json
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from config.settings import PRICE_CHANGE_THRESHOLD, OUTPUT_DIR

BASE_DIR = os.path.dirname(__file__)
HISTORY_FILE = os.path.join(OUTPUT_DIR, "price_history_v3.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(BASE_DIR, "output", "scheduler.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("Scheduler")

# ── 제품 카테고리 정의 ──────────────────────────────────
CATEGORIES = {
    "card": {
        "name": "명함",
        "reference": os.path.join(BASE_DIR, "config", "card_reference.json"),
        "ref_key": "papers",  # reference JSON에서 매칭 목록 키
    },
    "sticker": {
        "name": "스티커",
        "reference": os.path.join(BASE_DIR, "config", "sticker_reference.json"),
        "ref_key": "materials",
    },
    "envelope": {
        "name": "봉투",
        "reference": os.path.join(BASE_DIR, "config", "envelope_reference.json"),
        "ref_key": "papers",
    },
    "flyer": {
        "name": "전단지",
        "reference": os.path.join(BASE_DIR, "config", "flyer_reference.json"),
        "ref_key": "matches",
    },
    "postcard": {
        "name": "엽서",
        "reference": os.path.join(BASE_DIR, "config", "postcard_reference.json"),
        "ref_key": "papers",
    },
}


# ══════════════════════════════════════════════════════════
# 크롤러 실행 함수 (카테고리별)
# ══════════════════════════════════════════════════════════

# ── 명함 크롤러 ─────────────────────────────────────────
def run_card_ecard21() -> list[dict]:
    from crawlers.Ecard21CardCrawler import Ecard21Crawler
    target = get_target_paper_codes("card")
    crawler = Ecard21Crawler(headless=True, target_papers=target)
    logger.info(f"    필터: {len(target)}개 용지" if target else "    필터: 전체")
    try:
        crawler.crawl_all()
    finally:
        crawler.close()
    return [{"company": "ecard21", "product": r.product_name, "paper_code": r.paper_code,
             "paper_name": r.paper_name, "price": r.price, "quantity": r.quantity}
            for r in crawler.results]


def run_card_bizhows() -> list[dict]:
    from crawlers.BizhowsCardCrawler import BizhowsCrawler
    crawler = BizhowsCrawler(headless=True)
    crawler.run()
    results = []
    for r in crawler.results:
        if r.get("error"):
            continue
        price = None
        if r.get("price"):
            m = re.search(r"[\d,]+", str(r["price"]))
            if m:
                price = int(m.group().replace(",", ""))
        results.append({"company": "bizhows", "product": r.get("product", ""),
                        "paper_name": r.get("paper", ""), "price": price, "quantity": r.get("qty", "200매")})
    return results


def run_card_printcity() -> list[dict]:
    from crawlers.PrintcityCardCrawler import crawl_all, save_results
    data = crawl_all()
    save_results(data)
    results = []
    for product in data.get("제품별_가격", []):
        if "오류" in product:
            continue
        if "코팅옵션별_가격" in product:
            for cg in product["코팅옵션별_가격"]:
                for paper in cg.get("용지별_가격", []):
                    price = paper.get("총결제액(원)")
                    if price:
                        results.append({"company": "printcity", "product": product.get("상품명", ""),
                                        "paper_name": paper.get("용지", ""), "price": price,
                                        "quantity": paper.get("수량", 200)})
        elif "가격목록" in product:
            for paper in product.get("가격목록", []):
                price = paper.get("총결제액(원)")
                if price:
                    results.append({"company": "printcity", "product": product.get("상품명", ""),
                                    "paper_name": paper.get("용지", ""), "price": price,
                                    "quantity": paper.get("수량", 200)})
    return results


def run_card_naple() -> list[dict]:
    from crawlers.NapleCardCrawler import crawl_all, save_results
    all_rows = crawl_all()
    save_results(all_rows)
    results = []
    for r in all_rows:
        total = r.get("합계(VAT포함)", 0)
        delivery = r.get("배송비", 3000)
        price = total - delivery if total else None
        if price:
            results.append({"company": "naple", "product": r.get("제품명", ""),
                            "paper_name": r.get("용지명", ""), "price": price,
                            "quantity": r.get("수량", 200)})
    return results


def run_card_dtpia() -> list[dict]:
    from crawlers.DtpiaCardCrawler import main as dt_main
    dt_main()
    now_path = os.path.join(OUTPUT_DIR, "dtpia_card_now.json")
    if not os.path.exists(now_path):
        return []
    with open(now_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [{"company": "dtpia", "product": r.get("product", ""),
             "paper_name": r.get("paper_name", ""), "coating": r.get("coating", "없음"),
             "price": r.get("price"), "quantity": r.get("qty", 200)}
            for r in data.get("items", []) if r.get("price")]


def run_card_swadpia() -> list[dict]:
    from crawlers.SwadpiaCardCrawler import main as sw_main
    sw_main()
    # main()이 save_results까지 하므로 저장된 JSON에서 읽기
    now_path = os.path.join(OUTPUT_DIR, "swadpia_card_now.json")
    if not os.path.exists(now_path):
        return []
    with open(now_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [{"company": "swadpia", "product": r.get("product", ""),
             "paper_name": r.get("paper_name", ""), "price": r.get("price"),
             "quantity": r.get("qty", 200)}
            for r in data.get("items", []) if r.get("price")]




# ── 스티커 크롤러 ────────────────────────────────────────
def run_sticker_ecard21() -> list[dict]:
    from crawlers.Ecard21StickerCrawler import Ecard21StickerCrawler
    target = get_target_paper_codes("sticker")
    crawler = Ecard21StickerCrawler(headless=True, target_papers=target)
    logger.info(f"    필터: {len(target)}개 용지" if target else "    필터: 전체")
    try:
        crawler.crawl_all()
        crawler.save_json()
    finally:
        crawler.close()
    return [{"company": "ecard21", "product": r.product_name, "paper_code": r.paper_code,
             "paper_name": r.paper_name, "size": r.size_label, "price": r.price, "quantity": r.quantity}
            for r in crawler.results]


def run_sticker_bizhows() -> list[dict]:
    from crawlers.BizhowsStickerCrawler import BizhowsStickerCrawler
    crawler = BizhowsStickerCrawler(headless=True)
    crawler.run()
    results = []
    for r in crawler.results:
        price = None
        if r.get("price"):
            m = re.search(r"[\d,]+", str(r["price"]))
            if m:
                price = int(m.group().replace(",", ""))
        results.append({"company": "bizhows", "product": r.get("product", ""),
                        "paper_name": r.get("paper", ""), "size": r.get("size", ""),
                        "shape": r.get("shape", ""), "price": price, "quantity": r.get("qty", "1000")})
    return results


def run_sticker_ohprint() -> list[dict]:
    import asyncio
    from crawlers.OhprintStickerCrawler import main as oh_main
    oh_all = asyncio.run(oh_main())  # 크롤러가 JSON 저장 + 결과 반환
    if not oh_all:
        return []
    return [{"company": "ohprint", "product": r.get("제품", ""), "paper_name": r.get("용지", ""),
             "size": r.get("사이즈", ""), "shape": r.get("형태", ""),
             "price": r.get("판매가"), "quantity": r.get("수량", 1000)}
            for r in oh_all if not r.get("error")]


# ── 봉투 크롤러 ─────────────────────────────────────────
def run_envelope_ecard21() -> list[dict]:
    from crawlers.Ecard21EnvelopeCrawler import Ecard21EnvelopeCrawler
    target = get_target_paper_codes("envelope")
    crawler = Ecard21EnvelopeCrawler(headless=True, target_papers=target)
    logger.info(f"    필터: {len(target)}개 용지" if target else "    필터: 전체")
    try:
        crawler.crawl_all()
        crawler.save_json()
    finally:
        crawler.close()
    return [{"company": "ecard21", "product": r.product_name, "paper_code": r.paper_code,
             "paper_name": r.paper_name, "size": r.size_name, "price": r.price, "quantity": r.quantity}
            for r in crawler.results]


def run_envelope_bizhows() -> list[dict]:
    from crawlers.BizhowsEnvelopeCrawler import BizhowsEnvelopeCrawler
    crawler = BizhowsEnvelopeCrawler(headless=True)
    crawler.run()
    results = []
    for r in crawler.results:
        price = r.get("price")
        if isinstance(price, str):
            m = re.search(r"[\d,]+", price)
            price = int(m.group().replace(",", "")) if m else None
        results.append({"company": "bizhows", "product": r.get("product", ""),
                        "paper_name": r.get("paper", r.get("용지", "")),
                        "size": r.get("size", r.get("사이즈", "")),
                        "price": price, "quantity": r.get("qty", r.get("수량", 500))})
    return results


def run_envelope_ohprint() -> list[dict]:
    from crawlers.OhprintEnvelopeCrawler import main as oh_main
    oh_all = oh_main()  # 크롤러가 JSON 저장 + 결과 반환
    if not oh_all:
        return []
    return [{"company": "ohprint", "product": r.get("제품", r.get("product", "")),
             "paper_name": r.get("용지", r.get("paper", "")),
             "size": r.get("사이즈", r.get("size", "")),
             "price": r.get("가격_500매", r.get("price", r.get("판매가"))),
             "quantity": 500}
            for r in oh_all if not r.get("error")]


# ── 전단지 크롤러 ────────────────────────────────────────
def run_flyer_ecard21() -> list[dict]:
    from crawlers.Ecard21FlyerCrawler import Ecard21FlyerCrawler
    target = get_target_paper_codes("flyer")
    crawler = Ecard21FlyerCrawler(headless=True, target_papers=target)
    logger.info(f"    필터: {len(target)}개 용지" if target else "    필터: 전체")
    try:
        crawler.crawl_all()
        crawler.save_json()
    finally:
        crawler.close()
    return [{"company": "ecard21", "product": r.product_name, "paper_code": r.paper_code,
             "paper_name": r.paper_name, "price": r.price, "quantity": r.quantity}
            for r in crawler.results]


def run_flyer_bizhows() -> list[dict]:
    from crawlers.BizhowsFlyerCrawler import BizhowsFlyerCrawler
    crawler = BizhowsFlyerCrawler(headless=True)
    crawler.run()
    results = []
    for r in crawler.results:
        price = r.get("price")
        if isinstance(price, str):
            m = re.search(r"[\d,]+", str(price))
            price = int(m.group().replace(",", "")) if m else None
        results.append({"company": "bizhows", "product": r.get("product", ""),
                        "paper_name": r.get("paper", ""),
                        "price": price, "quantity": r.get("qty", 4000)})
    return results


# ── 엽서 크롤러 ─────────────────────────────────────────
def run_postcard_ecard21() -> list[dict]:
    from crawlers.Ecard21PostcardCrawler import Ecard21PostcardCrawler
    target = get_target_paper_codes("postcard")
    crawler = Ecard21PostcardCrawler(headless=True, target_papers=target)
    logger.info(f"    필터: {len(target)}개 용지" if target else "    필터: 전체")
    try:
        crawler.crawl()
        crawler.save_json()
    finally:
        crawler.close()
    return [{"company": "ecard21", "paper_code": r.paper_code,
             "paper_name": r.paper_name,
             "price": r.price, "quantity": r.quantity}
            for r in crawler.results]


def run_postcard_bizhows() -> list[dict]:
    from crawlers.BizhowsPostcardCrawler import BizhowsPostcardCrawler
    crawler = BizhowsPostcardCrawler(headless=True)
    crawler.run()
    results = []
    for r in crawler.results:
        price = r.get("price")
        if isinstance(price, str):
            m = re.search(r"[\d,]+", str(price))
            price = int(m.group().replace(",", "")) if m else None
        results.append({"company": "bizhows",
                        "paper_name": r.get("paper_name", ""),
                        "price": price, "quantity": r.get("quantity", 200)})
    return results


# ── 크롤러 맵 ────────────────────────────────────────────
CRAWLERS = {
    "card": [
        ("명함천국 명함", run_card_ecard21),
        ("비즈하우스 명함", run_card_bizhows),
        ("프린트시티 명함", run_card_printcity),
        ("나플 명함", run_card_naple),
        ("성원애드피아 명함", run_card_swadpia),
        ("디티피아 명함", run_card_dtpia),
    ],
    "sticker": [
        ("명함천국 스티커", run_sticker_ecard21),
        ("비즈하우스 스티커", run_sticker_bizhows),
        ("오프린트미 스티커", run_sticker_ohprint),
    ],
    "envelope": [
        ("명함천국 봉투", run_envelope_ecard21),
        ("비즈하우스 봉투", run_envelope_bizhows),
        ("오프린트미 봉투", run_envelope_ohprint),
    ],
    "flyer": [
        ("명함천국 전단지", run_flyer_ecard21),
        ("비즈하우스 전단지", run_flyer_bizhows),
    ],
    "postcard": [
        ("명함천국 엽서", run_postcard_ecard21),
        ("비즈하우스 엽서", run_postcard_bizhows),
    ],
}


# ══════════════════════════════════════════════════════════
# 공통 로직: reference 로드 / 필터링 / 변동감지 / 이메일
# ══════════════════════════════════════════════════════════

def load_reference(category: str) -> list[dict]:
    cat = CATEGORIES[category]
    ref_path = cat["reference"]
    if not os.path.exists(ref_path):
        logger.warning(f"{category} reference 파일 없음: {ref_path}")
        return []
    with open(ref_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ref_key = cat["ref_key"]
    items = data.get(ref_key, [])
    # ecard21이 있는 항목만
    return [p for p in items if p.get("ecard21")]


def get_target_paper_codes(category: str) -> list[str] | None:
    """reference에서 ecard21 paper_code 목록 추출 (크롤러 필터링용)"""
    ref = load_reference(category)
    if not ref:
        return None
    codes = []
    for item in ref:
        ec = item.get("ecard21") or {}
        code = ec.get("paper_code", "")
        if code:
            codes.append(code)
    return codes if codes else None


def filter_by_reference(crawl_results: list[dict], reference: list[dict]) -> list[dict]:
    by_company = {"ecard21": [], "bizhows": [], "printcity": [], "naple": [], "swadpia": [], "dtpia": []}
    for r in crawl_results:
        company = r.get("company", "")
        if company in by_company:
            by_company[company].append(r)

    filtered = []
    for ref in reference:
        entry = {
            "paper_id": ref.get("paper_id") or ref.get("material_id") or ref.get("match_id") or "",
            "paper_name_ko": ref.get("paper_name_ko") or ref.get("material_name_ko") or ref.get("name") or
                             f"{ref.get('paper_type', '')} {ref.get('paper_weight', '')}".strip() or "",
            "match_confidence": ref.get("match_confidence", ""),
        }

        # ecard21
        ec_ref = ref.get("ecard21") or {}
        ec_code = ec_ref.get("paper_code", "")
        entry["ecard21_price"] = None
        for r in by_company["ecard21"]:
            if ec_code and r.get("paper_code") == ec_code:
                entry["ecard21_price"] = r["price"]
                break

        # bizhows
        bz_ref = ref.get("bizhows") or {}
        bz_paper = bz_ref.get("paper_name") or bz_ref.get("paper") or ""
        entry["bizhows_price"] = None
        for r in by_company["bizhows"]:
            r_paper = r.get("paper_name", "")
            if r_paper and bz_paper and (bz_paper.lower() in r_paper.lower() or r_paper.lower() in bz_paper.lower()):
                entry["bizhows_price"] = r["price"]
                break

        # printcity — crawl_key 기반 매칭
        pc_ref = ref.get("printcity") or {}
        pc_key = pc_ref.get("crawl_key", "")
        entry["printcity_price"] = None
        for r in by_company["printcity"]:
            r_paper = r.get("paper_name", "")
            if r_paper and pc_key and (pc_key.lower() in r_paper.lower() or r_paper.lower() in pc_key.lower()):
                entry["printcity_price"] = r["price"]
                break

        # naple — crawl_key 기반 매칭
        np_ref = ref.get("naple") or {}
        np_key = np_ref.get("crawl_key", "")
        entry["naple_price"] = None
        for r in by_company["naple"]:
            r_paper = r.get("paper_name", "")
            if r_paper and np_key and (np_key.lower() in r_paper.lower() or r_paper.lower() in np_key.lower()):
                entry["naple_price"] = r["price"]
                break

        # swadpia — crawl_key 기반 매칭
        sw_ref = ref.get("swadpia") or {}
        sw_key = sw_ref.get("crawl_key", "")
        entry["swadpia_price"] = None
        for r in by_company["swadpia"]:
            r_paper = r.get("paper_name", "")
            if r_paper and sw_key and (sw_key.lower() in r_paper.lower() or r_paper.lower() in sw_key.lower()):
                entry["swadpia_price"] = r["price"]
                break

        # dtpia — crawl_key + (optional) coating 기반 매칭
        dt_ref = ref.get("dtpia") or {}
        dt_key = dt_ref.get("crawl_key", "")
        dt_coating = dt_ref.get("coating", "")  # 빈 값이면 코팅 무관
        entry["dtpia_price"] = None
        for r in by_company["dtpia"]:
            r_paper = r.get("paper_name", "")
            r_coating = r.get("coating", "")
            if not (r_paper and dt_key):
                continue
            if not (dt_key.lower() in r_paper.lower() or r_paper.lower() in dt_key.lower()):
                continue
            if dt_coating and dt_coating != r_coating:
                continue
            entry["dtpia_price"] = r["price"]
            break

        filtered.append(entry)

    return filtered


def load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(category: str, filtered: list[dict]):
    history = load_history()
    if category not in history:
        history[category] = {}
    for entry in filtered:
        pid = entry["paper_id"]
        history[category][pid] = {
            "ecard21": entry.get("ecard21_price"),
            "bizhows": entry.get("bizhows_price"),
            "printcity": entry.get("printcity_price"),
            "naple": entry.get("naple_price"),
            "swadpia": entry.get("swadpia_price"),
            "dtpia": entry.get("dtpia_price"),
            "updated_at": datetime.now().isoformat(),
        }
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    logger.info(f"히스토리 저장: {category} {len(filtered)}건")


def detect_changes(category: str, filtered: list[dict]) -> list[dict]:
    history = load_history()
    cat_history = history.get(category, {})
    changes = []
    threshold = PRICE_CHANGE_THRESHOLD

    for entry in filtered:
        pid = entry["paper_id"]
        prev = cat_history.get(pid, {})

        for company in ["bizhows", "printcity", "naple", "swadpia", "dtpia"]:
            cur_price = entry.get(f"{company}_price")
            prev_price = prev.get(company)

            if cur_price is None or prev_price is None:
                if cur_price is not None and prev_price is None:
                    changes.append({
                        "type": "new", "category": category,
                        "paper_id": pid, "paper_name_ko": entry["paper_name_ko"],
                        "company": company, "price": cur_price, "prev_price": None,
                        "ecard21_price": entry.get("ecard21_price"),
                    })
                continue

            if prev_price == 0:
                continue

            change_pct = ((cur_price - prev_price) / prev_price) * 100
            if abs(change_pct) >= threshold:
                changes.append({
                    "type": "up" if change_pct > 0 else "down", "category": category,
                    "paper_id": pid, "paper_name_ko": entry["paper_name_ko"],
                    "company": company, "price": cur_price, "prev_price": prev_price,
                    "change_pct": round(change_pct, 1),
                    "ecard21_price": entry.get("ecard21_price"),
                })

    return changes


# ══════════════════════════════════════════════════════════
# 메인 작업
# ══════════════════════════════════════════════════════════

def _run_single_crawler(args):
    """병렬 실행용 래퍼. (name, runner) → (name, results or error)"""
    name, runner = args
    try:
        results = runner()
        return (name, results, None)
    except Exception as e:
        return (name, [], str(e))


def run_category(category: str) -> tuple[list[dict], list[dict]]:
    """단일 카테고리 크롤링 → 필터링 → 변동감지. (filtered, changes) 반환"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cat_name = CATEGORIES[category]["name"]
    logger.info(f"\n{'='*50}")
    logger.info(f"[{cat_name}] 크롤링 시작 (병렬)")

    # 크롤러 병렬 실행
    all_results = []
    crawlers = CRAWLERS.get(category, [])
    with ThreadPoolExecutor(max_workers=len(crawlers)) as pool:
        futures = {pool.submit(_run_single_crawler, (name, runner)): name
                   for name, runner in crawlers}
        for future in as_completed(futures):
            name, results, error = future.result()
            if error:
                logger.error(f"  {name} 실패: {error}")
            else:
                all_results.extend(results)
                logger.info(f"  {name}: {len(results)}건")

    # reference 기반 필터링
    reference = load_reference(category)
    if not reference:
        logger.warning(f"  [{cat_name}] reference 없음 → 건너뜀")
        return [], []

    filtered = filter_by_reference(all_results, reference)
    logger.info(f"  [{cat_name}] 매칭: {len(filtered)}건 / 크롤링: {len(all_results)}건")

    # 변동 감지
    changes = detect_changes(category, filtered)
    price_changes = [c for c in changes if c["type"] in ("up", "down")]
    if price_changes:
        logger.info(f"  [{cat_name}] 변동 {len(price_changes)}건!")
        for c in price_changes:
            company = "비즈하우스" if c["company"] == "bizhows" else "오프린트미"
            logger.info(f"    {company} {c['paper_name_ko']}: {c['prev_price']:,} -> {c['price']:,} ({c['change_pct']:+.1f}%)")
    else:
        logger.info(f"  [{cat_name}] 변동 없음")

    # 히스토리 저장
    save_history(category, filtered)

    return filtered, changes


def job(categories=None):
    """전체 또는 지정 카테고리 실행 (카테고리 내 크롤러는 병렬)"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if categories is None:
        categories = ["card", "sticker", "envelope", "flyer", "postcard"]

    logger.info("=" * 60)
    logger.info(f"가격 모니터링 시작 ({', '.join(categories)}) - 병렬 실행")
    logger.info("=" * 60)

    all_changes = []
    summary = {}

    # 카테고리도 2개씩 병렬 (브라우저 메모리 고려)
    def _run_cat(category):
        try:
            filtered, changes = run_category(category)
            return category, filtered, changes, None
        except Exception as e:
            return category, [], [], str(e)

    batch_size = 2
    for i in range(0, len(categories), batch_size):
        batch = [c for c in categories[i:i+batch_size] if c in CATEGORIES]
        if not batch:
            continue
        logger.info(f"\n배치 실행: {', '.join(batch)}")
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = {pool.submit(_run_cat, cat): cat for cat in batch}
            for future in as_completed(futures):
                category, filtered, changes, error = future.result()
                if error:
                    logger.error(f"[{category}] 실패: {error}")
                    summary[category] = {"matched": 0, "crawled": 0}
                else:
                    all_changes.extend(changes)
                    summary[category] = {"matched": len(filtered), "crawled": len(filtered)}

    price_changes = [c for c in all_changes if c["type"] in ("up", "down")]
    logger.info("=" * 60)
    logger.info(f"가격 모니터링 완료: 변동 {len(price_changes)}건")
    logger.info("=" * 60)



def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    categories = sys.argv[1:] if len(sys.argv) > 1 else None
    job(categories)


if __name__ == "__main__":
    main()
