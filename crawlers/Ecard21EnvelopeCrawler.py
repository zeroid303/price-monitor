"""
명함천국(ecard21.co.kr) 봉투 제품 크롤러
- 각 봉투 제품 페이지에 접속하여 사이즈별 × 용지별 가격을 수집
- 고정 옵션: 인쇄도수(자동: 컬러→단면칼라, 흑백→단면흑백) / 500매(없으면 최소 수량) / 양면테잎 없음 / 코팅없음
- Selenium 기반 (동적 가격 계산 대응)
[봉투 제품 6종]
  - 컬러 대봉투: fileorder_envel_big.asp       → 사이즈 1개 (A4)
  - 컬러 중봉투: fileorder_envel_m.asp         → 사이즈 2개 (6절/9절)
  - 컬러 소봉투: fileorder_envel_s.asp         → 사이즈 2개 (일반/자켓)
  - 흑백 대봉투: fileorder_envel_bigblack.asp   → 사이즈 1개 (A4)
  - 흑백 중봉투: fileorder_envel_mblack.asp     → 사이즈 2개 (6절/9절)
  - 흑백 소봉투: fileorder_envel_sblack.asp     → 사이즈 2개 (일반/자켓)
[SELECT 요소 ID]
  paper_code       : 용지 선택
  color_code       : 인쇄도수 선택 (옵션 1개뿐 – 자동 선택됨)
  sizeinfo_code    : 사이즈 선택
  qty_code         : 수량 선택 (용지+사이즈 선택 후 동적 생성)
  envelope_tape    : 양면테잎 (N/Y)
  jumjiinfo_coating: 코팅 옵션
[가격 읽기]
  input#price (hidden) → value 에서 정수 읽기
"""
import time
import json
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
# ── 봉투 제품 목록 ──────────────────────────────────────────
ENVELOPE_PRODUCTS = [
    {
        "name": "컬러 대봉투",
        "path": "/product/card/fileorder/fileorder_envel_big.asp",
    },
    {
        "name": "컬러 중봉투",
        "path": "/product/card/fileorder/fileorder_envel_m.asp",
    },
    {
        "name": "컬러 소봉투",
        "path": "/product/card/fileorder/fileorder_envel_s.asp",
    },
    {
        "name": "흑백 대봉투",
        "path": "/product/card/fileorder/fileorder_envel_bigblack.asp",
    },
    {
        "name": "흑백 중봉투",
        "path": "/product/card/fileorder/fileorder_envel_mblack.asp",
    },
    {
        "name": "흑백 소봉투",
        "path": "/product/card/fileorder/fileorder_envel_sblack.asp",
    },
]
TARGET_QTY = 500  # 목표 수량
@dataclass
class EnvelopePriceRecord:
    """봉투 용지×사이즈별 가격 레코드"""
    product_name: str   # 제품명 (예: 컬러 대봉투)
    paper_code: str     # 용지 코드 (예: ppk22)
    paper_name: str     # 용지명 (예: 모조지 120g [봉투 기본용지])
    color_mode: str     # 인쇄도수 (예: 단면 칼라)
    size_code: str      # 사이즈 코드 (예: pss25)
    size_name: str      # 사이즈명 (예: 칼라대봉투 [A4 서류봉투] / 몸통 : 330 x 245mm ...)
    quantity: int       # 수량
    price: int          # 가격 (원)
    crawled_at: str     # 크롤링 시각
class Ecard21EnvelopeCrawler:
    """명함천국 봉투 제품 크롤러"""
    def __init__(self, headless: bool = True, target_papers: list[str] = None):
        """
        target_papers: 크롤링할 paper_code 목록. None이면 전체 수집.
                       예: ["ppk22", "ppk23"]
        """
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        # 불필요한 리소스 차단으로 속도 향상
        prefs = {"profile.managed_default_content_settings.images": 2}
        options.add_experimental_option("prefs", prefs)
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 15)
        self.results: list[EnvelopePriceRecord] = []
        self.target_papers = set(target_papers) if target_papers else None
    def close(self):
        self.driver.quit()
    # ── 유틸리티 ─────────────────────────────────────────────
    def _select_option_by_value(self, select_id: str, value: str) -> bool:
        """셀렉트 박스에서 값을 선택하고 jQuery change 이벤트를 트리거"""
        try:
            select_el = self.wait.until(
                EC.presence_of_element_located((By.ID, select_id))
            )
            sel = Select(select_el)
            sel.select_by_value(value)
            # jQuery change 이벤트 트리거 (가격 계산 함수 호출용)
            self.driver.execute_script(
                f"jQuery('#{select_id}').trigger('change');"
            )
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.warning(f"옵션 선택 실패 [{select_id}={value}]: {e}")
            return False
    def _get_select_options(self, select_id: str) -> list[dict]:
        """셀렉트 박스의 모든 유효 옵션 정보 반환 (빈 값 제외)"""
        try:
            select_el = self.driver.find_element(By.ID, select_id)
            options = []
            for opt in select_el.find_elements(By.TAG_NAME, "option"):
                val = opt.get_attribute("value")
                if val:  # 빈 값(placeholder) 제외
                    options.append({
                        "value": val,
                        "text": opt.text.strip(),
                    })
            return options
        except NoSuchElementException:
            return []
    def _get_price(self) -> int:
        """현재 설정된 옵션에 대한 가격을 hidden input에서 읽기"""
        try:
            price_input = self.driver.find_element(By.ID, "price")
            val = price_input.get_attribute("value")
            return int(val) if val and val.isdigit() else 0
        except Exception:
            return 0
    def _find_qty(self, target: int = TARGET_QTY) -> str | None:
        """
        목표 수량 옵션 확인.
        정확히 일치하는 값이 있으면 사용, 없으면 가장 가까운(큰 쪽 우선) 수량 반환.
        """
        options = self._get_select_options("qty_code")
        # 정확히 일치
        for opt in options:
            if opt["value"] == str(target):
                return str(target)
        # 없으면 target 이상인 값 중 최소값
        above = []
        for opt in options:
            try:
                qty = int(opt["value"])
                if qty >= target:
                    above.append(qty)
            except ValueError:
                continue
        if above:
            return str(min(above))
        # 그래도 없으면 가장 가까운 값
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
    def crawl_product(self, product: dict) -> list[EnvelopePriceRecord]:
        """
        단일 봉투 제품 페이지에서 사이즈별 × 용지별 가격 수집.
        순서: 사이즈 루프 > 용지 루프 > 수량 선택 > 가격 읽기
        """
        product_name = product["name"]
        url = BASE_URL + product["path"]
        logger.info(f"{'='*50}")
        logger.info(f"▶ [{product_name}] 페이지 접속: {url}")
        self.driver.get(url)
        time.sleep(2)  # JS 로딩 대기
        # 1) 사이즈 옵션 목록 수집
        size_options = self._get_select_options("sizeinfo_code")
        if not size_options:
            logger.warning(f"  [{product_name}] 사이즈 옵션 없음 - 건너뜀")
            return []
        logger.info(f"  [{product_name}] 사이즈 {len(size_options)}개 발견")
        # 2) 용지 옵션 목록 수집
        paper_options = self._get_select_options("paper_code")
        if not paper_options:
            logger.warning(f"  [{product_name}] 용지 옵션 없음 - 건너뜀")
            return []
        logger.info(f"  [{product_name}] 용지 {len(paper_options)}개 발견")
        # 3) 인쇄도수 (옵션 1개뿐이므로 텍스트만 가져옴)
        color_options = self._get_select_options("color_code")
        color_text = color_options[0]["text"] if color_options else "알 수 없음"
        color_value = color_options[0]["value"] if color_options else None
        records = []
        for size in size_options:
            size_code = size["value"]
            size_name = size["text"]
            logger.info(f"\\n  ── 사이즈: {size_name}")
            for paper in paper_options:
                paper_code = paper["value"]
                # target_papers 필터링
                if self.target_papers and paper_code not in self.target_papers:
                    continue
                paper_name = paper["text"]
                try:
                    # a) 용지 선택
                    if not self._select_option_by_value("paper_code", paper_code):
                        continue
                    time.sleep(0.3)
                    # b) 인쇄도수 선택 (옵션 1개뿐이지만 명시적으로 선택)
                    if color_value:
                        self._select_option_by_value("color_code", color_value)
                        time.sleep(0.3)
                    # c) 사이즈 선택
                    if not self._select_option_by_value("sizeinfo_code", size_code):
                        continue
                    time.sleep(1.0)  # 수량 옵션 동적 생성 대기
                    # d) 수량 선택 (500매 목표)
                    qty_value = self._find_qty(TARGET_QTY)
                    if not qty_value:
                        logger.warning(f"    [{paper_name}] 수량 옵션 없음")
                        continue
                    self._select_option_by_value("qty_code", qty_value)
                    time.sleep(0.8)  # 가격 계산 대기
                    # e) 가격 읽기
                    price = self._get_price()
                    record = EnvelopePriceRecord(
                        product_name=product_name,
                        paper_code=paper_code,
                        paper_name=paper_name,
                        color_mode=color_text,
                        size_code=size_code,
                        size_name=size_name,
                        quantity=int(qty_value),
                        price=price,
                        crawled_at=datetime.now().isoformat(),
                    )
                    records.append(record)
                    logger.info(
                        f"    ✓ {paper_name} | {qty_value}매 | ₩{price:,}"
                    )
                except (StaleElementReferenceException, TimeoutException) as e:
                    logger.warning(f"    [{paper_name}] 크롤링 실패: {e}")
                    # 페이지 새로고침 후 다음 용지로
                    self.driver.get(url)
                    time.sleep(2)
                    continue
        return records
    def crawl_all(self) -> list[EnvelopePriceRecord]:
        """모든 봉투 제품 크롤링"""
        logger.info("=" * 60)
        logger.info("명함천국 봉투 크롤링 시작")
        logger.info(f"목표 수량: {TARGET_QTY}매 (없으면 최소 수량)")
        logger.info("=" * 60)
        all_records = []
        for product in ENVELOPE_PRODUCTS:
            try:
                records = self.crawl_product(product)
                all_records.extend(records)
                logger.info(
                    f"\\n  [{product['name']}] 완료: {len(records)}개 레코드 수집\\n"
                )
            except Exception as e:
                logger.error(f"  [{product['name']}] 오류: {e}")
        self.results = all_records
        logger.info("=" * 60)
        logger.info(f"크롤링 완료: 총 {len(all_records)}개 레코드 수집")
        logger.info("=" * 60)
        return all_records
    def save_json(self, filepath: str = None):
        """결과를 JSON 파일로 저장"""
        import os
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if filepath is None:
            os.makedirs("output", exist_ok=True)
            filepath = f"output/ecard21_envelope_{timestamp}.json"
        data = {
            "crawled_at": datetime.now().isoformat(),
            "source": "ecard21.co.kr",
            "product_type": "봉투",
            "fixed_options": {
                "color": "인쇄도수 기본값 (컬러→단면칼라 / 흑백→단면흑백)",
                "target_quantity": TARGET_QTY,
                "envelope_tape": "뚜껑 양면테잎 없음",
                "coating": "코팅없음",
            },
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
# ── 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    crawler = Ecard21EnvelopeCrawler(headless=True)
    try:
        crawler.crawl_all()
        crawler.save_json()
    finally:
        crawler.close()