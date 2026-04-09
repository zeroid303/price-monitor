"""
명함천국(ecard21.co.kr) 명함 제품 크롤러
- 각 명함 제품 페이지에 접속하여 용지별 가격을 수집
- 고정 옵션: 양면칼라 / 기본사이즈(90x50 또는 86x54) / 200장
- Selenium 기반 (동적 가격 계산 대응)
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
# ── 명함 제품 목록 ──────────────────────────────────────────
CARD_PRODUCTS = [
    {"name": "기본 명함",       "path": "/product/card/fileorder/fileorder_basic.asp"},
    {"name": "2단 명함",        "path": "/product/card/fileorder/fileorder_coupon.asp"},
    {"name": "카드명함",        "path": "/product/card/fileorder/fileorder_card.asp"},
    {"name": "에폭시 명함",     "path": "/product/card/fileorder/fileorder_epoxy.asp"},
    {"name": "부분코팅 명함",   "path": "/product/card/fileorder/fileorder_spot.asp"},
    {"name": "화이트 명함",     "path": "/product/card/fileorder/fileorder_digital_whitecard.asp"},
    {"name": "형광 명함",       "path": "/product/card/fileorder/fileorder_neoncard.asp"},
    {"name": "엣지 명함",       "path": "/product/card/fileorder/fileorder_edge.asp"},
    {"name": "PET 카드명함",    "path": "/product/card/fileorder/fileorder_pet.asp"},
    {"name": "3D 금박명함",     "path": "/product/card/fileorder/fileorder_3dgold.asp"},
]
@dataclass
class PaperPriceRecord:
    """용지별 가격 레코드"""
    product_name: str       # 제품명 (예: 기본 명함)
    paper_code: str         # 용지 코드 (예: ppk31)
    paper_name: str         # 용지명 (예: 누브지 209g / 비코팅)
    color_mode: str         # 인쇄도수 (예: 양면 칼라)
    size: str               # 사이즈 (예: 90 x 50 mm)
    quantity: int            # 수량 (200)
    price: int              # 가격 (원)
    crawled_at: str         # 크롤링 시각
class Ecard21Crawler:
    """명함천국 명함 제품 크롤러"""
    def __init__(self, headless: bool = True, target_papers: list[str] = None):
        """
        target_papers: 크롤링할 paper_code 목록. None이면 전체 수집.
                       예: ["ppk31", "ppk30", "ppk16"]
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
        self.results: list[PaperPriceRecord] = []
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
        """셀렉트 박스의 모든 옵션 정보 반환"""
        try:
            select_el = self.driver.find_element(By.ID, select_id)
            options = []
            for opt in select_el.find_elements(By.TAG_NAME, "option"):
                val = opt.get_attribute("value")
                if val:  # 빈 값(placeholder) 제외
                    options.append({
                        "value": val,
                        "text": opt.text.strip(),
                        "code_price": opt.get_attribute("code-price") or "",
                    })
            return options
        except NoSuchElementException:
            return []
    def _get_price(self) -> int:
        """현재 설정된 옵션에 대한 가격을 hidden input에서 읽기"""
        try:
            price_input = self.driver.find_element(
                By.CSS_SELECTOR, "input[name='price']"
            )
            val = price_input.get_attribute("value")
            return int(val) if val and val.isdigit() else 0
        except Exception:
            return 0
    def _find_double_side_color(self) -> str | None:
        """양면 칼라 옵션의 value 반환 (제품마다 코드가 다름)"""
        options = self._get_select_options("color_code")
        for opt in options:
            text = opt["text"]
            # '양면' 키워드가 포함된 옵션 중 가장 첫 번째
            if "양면" in text and ("칼라" in text or "칼러" in text or "컬러" in text or "8도" in text or "인쇄" in text):
                return opt["value"]
        # 양면 옵션이 없으면 첫 번째 옵션 사용
        return options[0]["value"] if options else None
    def _find_default_size(self) -> str | None:
        """기본 사이즈(90x50 또는 첫 번째 사이즈) 옵션의 value 반환"""
        options = self._get_select_options("sizeinfo_code")
        # 90x50 우선
        for opt in options:
            if "90" in opt["text"] and "50" in opt["text"]:
                return opt["value"]
        # 86x54 (카드명함 등)
        for opt in options:
            if "86" in opt["text"] and "54" in opt["text"]:
                return opt["value"]
        # 그 외 첫 번째 유효 옵션
        return options[0]["value"] if options else None
    def _find_qty_200(self) -> str | None:
        """수량 200장 옵션 확인, 없으면 가장 가까운 수량 반환"""
        options = self._get_select_options("qty_code")
        for opt in options:
            if opt["value"] == "200":
                return "200"
        # 200이 없으면 가장 가까운 값
        closest = None
        min_diff = float("inf")
        for opt in options:
            try:
                qty = int(opt["value"])
                diff = abs(qty - 200)
                if diff < min_diff:
                    min_diff = diff
                    closest = opt["value"]
            except ValueError:
                continue
        return closest
    # ── 제품별 크롤링 ────────────────────────────────────────
    def crawl_product(self, product: dict) -> list[PaperPriceRecord]:
        """단일 제품 페이지에서 모든 용지별 가격 수집"""
        product_name = product["name"]
        url = BASE_URL + product["path"]
        logger.info(f"▶ [{product_name}] 페이지 접속: {url}")
        self.driver.get(url)
        time.sleep(2)  # JS 로딩 대기
        # 1) 용지 옵션 목록 수집
        paper_options = self._get_select_options("paper_code")
        if not paper_options:
            logger.warning(f"  [{product_name}] 용지 옵션 없음 - 건너뜀")
            return []
        logger.info(f"  [{product_name}] 용지 {len(paper_options)}개 발견")
        # 2) 양면 칼라 코드 확인
        color_value = self._find_double_side_color()
        if not color_value:
            logger.warning(f"  [{product_name}] 양면칼라 옵션 없음")
            return []
        records = []
        seen_papers = set()  # 중복 방지 (인기용지 BEST 5 영역과 전체 목록 중복)
        for paper in paper_options:
            paper_code = paper["value"]
            # 중복 용지 건너뛰기
            if paper_code in seen_papers:
                continue
            seen_papers.add(paper_code)
            # target_papers 필터링
            if self.target_papers and paper_code not in self.target_papers:
                continue
            paper_name = paper["text"]
            # "1위. " 등 순위 접두사 제거
            clean_name = paper_name
            for prefix in ["1위. ", "2위. ", "3위. ", "4위. ", "5위. "]:
                clean_name = clean_name.replace(prefix, "")
            try:
                # a) 용지 선택
                if not self._select_option_by_value("paper_code", paper_code):
                    continue
                time.sleep(0.3)
                # b) 양면 칼라 선택
                self._select_option_by_value("color_code", color_value)
                time.sleep(0.3)
                # c) 기본 사이즈 선택
                size_value = self._find_default_size()
                if not size_value:
                    logger.warning(f"    [{clean_name}] 사이즈 옵션 없음")
                    continue
                self._select_option_by_value("sizeinfo_code", size_value)
                time.sleep(0.5)  # 수량 옵션이 동적 생성되므로 대기
                # d) 200장 선택
                qty_value = self._find_qty_200()
                if not qty_value:
                    logger.warning(f"    [{clean_name}] 200장 옵션 없음")
                    continue
                self._select_option_by_value("qty_code", qty_value)
                time.sleep(0.5)  # 가격 계산 대기
                # e) 가격 읽기
                price = self._get_price()
                # 사이즈 텍스트 가져오기
                size_options = self._get_select_options("sizeinfo_code")
                size_text = ""
                for s in size_options:
                    if s["value"] == size_value:
                        size_text = s["text"]
                        break
                # 칼라 텍스트
                color_options = self._get_select_options("color_code")
                color_text = ""
                for c in color_options:
                    if c["value"] == color_value:
                        color_text = c["text"]
                        break
                record = PaperPriceRecord(
                    product_name=product_name,
                    paper_code=paper_code,
                    paper_name=clean_name,
                    color_mode=color_text,
                    size=size_text,
                    quantity=int(qty_value),
                    price=price,
                    crawled_at=datetime.now().isoformat(),
                )
                records.append(record)
                logger.info(
                    f"    ✓ {clean_name} | {color_text} | {size_text} | "
                    f"{qty_value}장 | ₩{price:,}"
                )
            except (StaleElementReferenceException, TimeoutException) as e:
                logger.warning(f"    [{clean_name}] 크롤링 실패: {e}")
                # 페이지 새로고침 후 재시도
                self.driver.get(url)
                time.sleep(2)
                continue
        return records
    def crawl_all(self) -> list[PaperPriceRecord]:
        """모든 명함 제품 크롤링"""
        logger.info("=" * 60)
        logger.info("명함천국 크롤링 시작")
        logger.info("=" * 60)
        all_records = []
        for product in CARD_PRODUCTS:
            try:
                records = self.crawl_product(product)
                all_records.extend(records)
                logger.info(
                    f"  [{product['name']}] 완료: {len(records)}개 용지 수집"
                )
            except Exception as e:
                logger.error(f"  [{product['name']}] 오류: {e}")
        self.results = all_records
        logger.info("=" * 60)
        logger.info(f"크롤링 완료: 총 {len(all_records)}개 레코드 수집")
        logger.info("=" * 60)
        return all_records
    def save_json(self, filepath: str = None):
        """결과를 JSON 파일로 저장 (타임스탬프 포함)"""
        import os
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if filepath is None:
            os.makedirs("output", exist_ok=True)
            filepath = f"output/ecard21_card_{timestamp}.json"
        data = {
            "crawled_at": datetime.now().isoformat(),
            "source": "ecard21.co.kr",
            "fixed_options": {
                "color": "양면 칼라",
                "size": "기본 사이즈 (90x50mm 우선)",
                "quantity": 200,
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
    crawler = Ecard21Crawler(headless=True)
    try:
        crawler.crawl_all()
        crawler.save_json()
    finally:
        crawler.close()