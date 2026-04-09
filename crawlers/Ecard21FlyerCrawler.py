"""
명함천국(ecard21.co.kr) 전단지 제품 크롤러
- 각 전단지 제품 페이지에 접속하여 용지별 가격을 수집
- 고정 옵션: A4 사이즈 / 양면 칼라
- 매수: 고급 독판 전단지 = 500매, 나머지 = 4,000매
- 문어발·문고리 등 A4 변형 사이즈가 여러 개인 제품은 A4 포함 사이즈 전부 수집
- 포스터, 접지 리플렛 제외
- Selenium 기반 (동적 가격 계산 대응)
"""
import time
import json
import csv
import logging
from datetime import datetime
from dataclasses import dataclass, asdict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
BASE_URL = "https://www.ecard21.co.kr"
# ── 전단지 제품 목록 (포스터·접지 리플렛 제외) ──────────────────
# target_qty: 제품별 수집 매수 (고급독판=500, 나머지=4000)
FLYER_PRODUCTS = [
    {
        "name": "일반 합판 전단지",
        "path": "/product/card/fileorder/fileorder_jundan_basic.asp",
        "target_qty": 4000,
    },
    {
        "name": "고급 독판 전단지",
        "path": "/product/card/fileorder/fileorder_jundan_special.asp",
        "target_qty": 500,
    },
    {
        "name": "프리미엄 에코 전단지",
        "path": "/product/card/fileorder/fileorder_jundan_eco.asp",
        "target_qty": 4000,
    },
    {
        "name": "문어발 전단지",
        "path": "/product/card/fileorder/fileorder_jundan_mun.asp",
        "target_qty": 4000,
    },
    {
        "name": "테이블 세팅지",
        "path": "/product/card/fileorder/fileorder_jundan_setting.asp",
        "target_qty": 4000,
    },
    {
        "name": "문고리 전단지",
        "path": "/product/card/fileorder/fileorder_jundan_mungo.asp",
        "target_qty": 4000,
    },
]
# ── 고정 옵션 ────────────────────────────────────────────────
FIXED_COLOR = "cld08"       # 양면 칼라 코드 (전 제품 공통)
FIXED_SIZE_KEYWORD = "A4"   # 사이즈 필터 키워드
@dataclass
class FlyerPriceRecord:
    """전단지 용지별 가격 레코드"""
    product_name: str       # 제품명 (예: 일반 합판 전단지)
    paper_code: str         # 용지 코드 (예: ppk90)
    paper_name: str         # 용지명 (예: 아트지 90g)
    color_name: str         # 인쇄도수명 (예: 양면 칼라) — 고정
    size_code: str          # 사이즈 코드 (예: pss17)
    size_name: str          # 사이즈명 (예: A4 (국 8절) / 210 x 297mm)
    quantity: int           # 수량 (4000 또는 500)
    price: int              # 가격 (원, VAT 포함)
    crawled_at: str         # 크롤링 시각
class Ecard21FlyerCrawler:
    """명함천국 전단지 제품 크롤러"""
    def __init__(self, headless: bool = True, target_papers: list[str] = None):
        """
        target_papers: 크롤링할 paper_code 목록. None이면 전체 수집.
                       예: ["ppk90", "ppk91"]
        """
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        prefs = {"profile.managed_default_content_settings.images": 2}
        options.add_experimental_option("prefs", prefs)
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 15)
        self.results: list[FlyerPriceRecord] = []
        self.target_papers = set(target_papers) if target_papers else None
    def close(self):
        self.driver.quit()
    # ── 유틸리티 ─────────────────────────────────────────────
    def _select_option_by_value(self, select_id: str, value: str) -> bool:
        try:
            select_el = self.wait.until(
                EC.presence_of_element_located((By.ID, select_id))
            )
            sel = Select(select_el)
            sel.select_by_value(value)
            self.driver.execute_script(
                f"jQuery('#{select_id}').trigger('change');"
            )
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.warning(f"옵션 선택 실패 [{select_id}={value}]: {e}")
            return False
    def _get_select_options(self, select_id: str) -> list[dict]:
        try:
            select_el = self.driver.find_element(By.ID, select_id)
            options = []
            for opt in select_el.find_elements(By.TAG_NAME, "option"):
                val = opt.get_attribute("value")
                if val:
                    options.append({
                        "value": val,
                        "text": opt.text.strip(),
                    })
            return options
        except NoSuchElementException:
            return []
    def _get_price(self) -> int:
        try:
            price_input = self.driver.find_element(
                By.CSS_SELECTOR, "input[name='price']"
            )
            val = price_input.get_attribute("value")
            return int(val) if val and val.isdigit() else 0
        except Exception:
            try:
                total_el = self.driver.find_element(By.ID, "total_msg_price")
                text = total_el.text.replace(",", "").strip()
                return int(text) if text.isdigit() else 0
            except Exception:
                return 0
    def _find_a4_sizes(self) -> list[dict]:
        options = self._get_select_options("sizeinfo_code")
        a4_options = [
            opt for opt in options
            if FIXED_SIZE_KEYWORD in opt["text"]
        ]
        return a4_options
    def _find_closest_qty(self, target: int) -> str | None:
        options = self._get_select_options("qty_code")
        if not options:
            return None
        # 정확히 일치 우선
        for opt in options:
            try:
                if int(opt["value"]) == target:
                    return opt["value"]
            except ValueError:
                continue
        # 가장 가까운 값
        closest = None
        min_diff = float("inf")
        for opt in options:
            try:
                qty = int(opt["value"])
                diff = abs(qty - target)
                if diff < min_diff:
                    min_diff = diff
                    closest = opt["value"]
            except ValueError:
                continue
        return closest
    # ── 제품별 크롤링 ────────────────────────────────────────
    def crawl_product(self, product: dict) -> list[FlyerPriceRecord]:
        product_name = product["name"]
        target_qty = product["target_qty"]
        url = BASE_URL + product["path"]
        logger.info(f"▶ [{product_name}] 페이지 접속: {url}")
        logger.info(f"  수집 매수: {target_qty:,}매")
        self.driver.get(url)
        time.sleep(2)
        # 1) 용지 옵션 목록
        paper_options = self._get_select_options("paper_code")
        if not paper_options:
            logger.warning(f"  [{product_name}] 용지 옵션 없음 - 건너뜀")
            return []
        logger.info(f"  [{product_name}] 용지 {len(paper_options)}개 발견")
        # 2) 양면 칼라 확인
        color_options = self._get_select_options("color_code")
        has_double_color = any(c["value"] == FIXED_COLOR for c in color_options)
        if not has_double_color:
            logger.warning(f"  [{product_name}] 양면 칼라(cld08) 옵션 없음")
            return []
        color_name = "양면 칼라"
        for c in color_options:
            if c["value"] == FIXED_COLOR:
                color_name = c["text"]
                break
        # 3) A4 사이즈 확인
        a4_sizes = self._find_a4_sizes()
        if not a4_sizes:
            logger.warning(f"  [{product_name}] A4 사이즈 옵션 없음 - 건너뜀")
            return []
        logger.info(
            f"  [{product_name}] A4 사이즈 {len(a4_sizes)}개: "
            f"{[s['text'][:30] for s in a4_sizes]}"
        )
        records = []
        for paper in paper_options:
            paper_code = paper["value"]
            # target_papers 필터링
            if self.target_papers and paper_code not in self.target_papers:
                continue
            paper_name = paper["text"]
            for size in a4_sizes:
                size_code = size["value"]
                size_name = size["text"]
                try:
                    # a) 용지 선택
                    if not self._select_option_by_value("paper_code", paper_code):
                        continue
                    time.sleep(0.3)
                    # b) 양면 칼라 선택
                    if not self._select_option_by_value("color_code", FIXED_COLOR):
                        continue
                    time.sleep(0.3)
                    # c) A4 사이즈 선택
                    if not self._select_option_by_value("sizeinfo_code", size_code):
                        continue
                    time.sleep(1.0)
                    # d) 매수 선택 (제품별 target_qty 적용)
                    qty_value = self._find_closest_qty(target_qty)
                    if not qty_value:
                        logger.warning(
                            f"    [{paper_name}|{size_name}] 수량 옵션 없음"
                        )
                        continue
                    if not self._select_option_by_value("qty_code", qty_value):
                        continue
                    time.sleep(0.8)
                    # e) 가격 읽기
                    price = self._get_price()
                    record = FlyerPriceRecord(
                        product_name=product_name,
                        paper_code=paper_code,
                        paper_name=paper_name,
                        color_name=color_name,
                        size_code=size_code,
                        size_name=size_name,
                        quantity=int(qty_value),
                        price=price,
                        crawled_at=datetime.now().isoformat(),
                    )
                    records.append(record)
                    logger.info(
                        f"    ✓ {paper_name} | {color_name} | "
                        f"{size_name[:30]} | {qty_value}매 | ₩{price:,}"
                    )
                except (StaleElementReferenceException, TimeoutException) as e:
                    logger.warning(
                        f"    [{paper_name}|{size_name}] 크롤링 실패: {e}"
                    )
                    self.driver.get(url)
                    time.sleep(2)
                    continue
        return records
    def crawl_all(self) -> list[FlyerPriceRecord]:
        logger.info("=" * 60)
        logger.info("명함천국 전단지 크롤링 시작")
        logger.info("고정 옵션: A4 / 양면 칼라")
        logger.info("매수: 고급독판=500매, 나머지=4,000매")
        logger.info("=" * 60)
        all_records = []
        for product in FLYER_PRODUCTS:
            try:
                records = self.crawl_product(product)
                all_records.extend(records)
                logger.info(
                    f"  [{product['name']}] 완료: "
                    f"{len(records)}개 용지 수집"
                )
            except Exception as e:
                logger.error(f"  [{product['name']}] 오류: {e}")
        self.results = all_records
        logger.info("=" * 60)
        logger.info(f"크롤링 완료: 총 {len(all_records)}개 레코드 수집")
        logger.info("=" * 60)
        return all_records
    def save_json(self, filepath: str = None):
        import os
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if filepath is None:
            os.makedirs("output", exist_ok=True)
            filepath = f"output/ecard21_flyer_{timestamp}.json"
        data = {
            "crawled_at": datetime.now().isoformat(),
            "source": "ecard21.co.kr",
            "category": "전단지",
            "fixed_options": {
                "color": "양면 칼라",
                "size": "A4",
                "quantity_note": "고급독판=500매, 나머지=4000매",
            },
            "excluded": ["대형 포스터", "접지 리플렛"],
            "total_records": len(self.results),
            "products": {},
        }
        for record in self.results:
            pname = record.product_name
            if pname not in data["products"]:
                data["products"][pname] = []
            data["products"][pname].append(asdict(record))
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"결과 저장: {filepath}")
    def save_csv(self, filepath: str = "ecard21_flyer_prices.csv"):
        if not self.results:
            logger.warning("저장할 데이터 없음")
            return
        fieldnames = [
            "product_name", "paper_code", "paper_name",
            "color_name", "size_code", "size_name",
            "quantity", "price", "crawled_at",
        ]
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in self.results:
                writer.writerow(asdict(record))
        logger.info(f"CSV 저장: {filepath}")
if __name__ == "__main__":
    crawler = Ecard21FlyerCrawler(headless=True)
    try:
        crawler.crawl_all()
        crawler.save_json()
        crawler.save_csv()
    finally:
        crawler.close()