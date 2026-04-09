"""
명함천국(ecard21.co.kr) 엽서 제품 크롤러
- 엽서 제품 페이지에 접속하여 용지별 가격을 수집
- 고정 옵션: 양면칼라 / 100x148mm / 200장
- Selenium 기반 (동적 가격 계산 대응)
"""
import time
import json
import logging
from datetime import datetime
from dataclasses import dataclass, asdict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
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
POSTCARD_PATH = "/product/card/fileorder/fileorder_postcard.asp"
# ── 고정 옵션 ──────────────────────────────────────────────
FIXED_COLOR_CODE = "cld08"       # 양면 칼라
FIXED_SIZE_INDEX = 2             # 100 x 148 mm (selectedIndex=2, value="postcard"와 90x140 동일하므로 index 필수)
FIXED_QTY = "200"                # 200장
@dataclass
class PostcardPriceRecord:
    """용지별 가격 레코드"""
    paper_code: str         # 용지 코드 (예: ppk31)
    paper_name: str         # 용지명 (예: 누브지 209g / 비코팅)
    color_mode: str         # 인쇄도수 (예: 양면 칼라)
    size: str               # 사이즈 (예: 100 x 148 mm)
    quantity: int           # 수량 (200)
    price: int              # 가격 (원, VAT 포함)
    crawled_at: str         # 크롤링 시각
class Ecard21PostcardCrawler:
    """명함천국 엽서 제품 크롤러"""
    def __init__(self, headless: bool = True, target_papers: list[str] = None):
        """
        target_papers: 크롤링할 paper_code 목록. None이면 전체 수집.
                       예: ["ppk31", "ppk30"]
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
        self.results: list[PostcardPriceRecord] = []
        self.target_papers = set(target_papers) if target_papers else None
    def close(self):
        self.driver.quit()
    # ── 유틸리티 ─────────────────────────────────────────────
    def _select_by_value(self, select_id: str, value: str) -> bool:
        """셀렉트 박스에서 값(value)을 선택하고 change 이벤트 트리거"""
        try:
            self.driver.execute_script(f"""
                var el = document.getElementById('{select_id}');
                el.value = '{value}';
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            """)
            return True
        except Exception as e:
            logger.warning(f"옵션 선택 실패 [{select_id}={value}]: {e}")
            return False
    def _select_by_index(self, select_id: str, index: int) -> bool:
        """셀렉트 박스에서 인덱스로 선택하고 change 이벤트 트리거
        (사이즈처럼 동일 value가 여러 개인 경우 사용)
        """
        try:
            self.driver.execute_script(f"""
                var el = document.getElementById('{select_id}');
                el.selectedIndex = {index};
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            """)
            return True
        except Exception as e:
            logger.warning(f"인덱스 선택 실패 [{select_id} index={index}]: {e}")
            return False
    def _get_unique_paper_options(self) -> list[dict]:
        """용지 셀렉트 박스에서 중복 제거한 옵션 목록 반환"""
        script = """
            var sel = document.getElementById('paper_code');
            var result = [];
            var seen = new Set();
            for (var i = 0; i < sel.options.length; i++) {
                var opt = sel.options[i];
                if (opt.value && !seen.has(opt.value)) {
                    seen.add(opt.value);
                    var text = opt.text.replace(/^\\\\d위\\\\.\\\\s*/, '');
                    result.push({ value: opt.value, text: text });
                }
            }
            return result;
        """
        return self.driver.execute_script(script)
    def _get_price(self) -> int:
        """현재 설정된 옵션에 대한 가격을 hidden input에서 읽기"""
        try:
            val = self.driver.execute_script(
                "return document.getElementById('price').value;"
            )
            return int(val) if val and val.isdigit() else 0
        except Exception:
            return 0
    def _get_selected_text(self, select_id: str) -> str:
        """현재 선택된 옵션의 텍스트 반환"""
        try:
            return self.driver.execute_script(f"""
                var el = document.getElementById('{select_id}');
                return el.options[el.selectedIndex].text;
            """)
        except Exception:
            return ""
    def _check_qty_has_200(self) -> bool:
        """수량 옵션에 200장이 존재하는지 확인"""
        script = """
            var sel = document.getElementById('qty_code');
            for (var i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value === '200') return true;
            }
            return false;
        """
        return self.driver.execute_script(script)
    # ── 크롤링 ──────────────────────────────────────────────
    def crawl(self) -> list[PostcardPriceRecord]:
        """엽서 페이지에서 모든 용지별 가격 수집"""
        url = BASE_URL + POSTCARD_PATH
        logger.info(f"▶ 엽서 페이지 접속: {url}")
        self.driver.get(url)
        time.sleep(3)  # JS 로딩 대기
        # 1) 용지 옵션 목록 수집 (중복 제거)
        paper_options = self._get_unique_paper_options()
        if not paper_options:
            logger.warning("용지 옵션 없음 - 크롤링 중단")
            return []
        logger.info(f"용지 {len(paper_options)}개 발견")
        records = []
        for paper in paper_options:
            paper_code = paper["value"]
            # target_papers 필터링
            if self.target_papers and paper_code not in self.target_papers:
                continue
            paper_name = paper["text"]
            try:
                # a) 용지 선택 → 다른 옵션들이 동적으로 갱신됨 (수량 리셋됨)
                if not self._select_by_value("paper_code", paper_code):
                    continue
                time.sleep(1.5)  # 옵션 갱신 대기
                # b) 양면 칼라 선택
                self._select_by_value("color_code", FIXED_COLOR_CODE)
                time.sleep(0.5)
                # c) 사이즈 선택 (100x148mm, index=2)
                #    ※ 90x140과 100x148 모두 value="postcard"이므로 index로 선택
                self._select_by_index("sizeinfo_code", FIXED_SIZE_INDEX)
                time.sleep(1.0)  # 수량 옵션 재생성 대기
                # d) 200장 선택 (용지/사이즈 변경 시 수량 리셋되므로 반드시 마지막에)
                if not self._check_qty_has_200():
                    logger.warning(f"  [{paper_name}] 200장 옵션 없음 - 건너뜀")
                    continue
                self._select_by_value("qty_code", FIXED_QTY)
                time.sleep(1.0)  # 가격 계산 대기
                # e) 가격 읽기
                price = self._get_price()
                # f) 선택된 옵션 텍스트 가져오기
                color_text = self._get_selected_text("color_code")
                size_text = self._get_selected_text("sizeinfo_code")
                record = PostcardPriceRecord(
                    paper_code=paper_code,
                    paper_name=paper_name,
                    color_mode=color_text,
                    size=size_text,
                    quantity=int(FIXED_QTY),
                    price=price,
                    crawled_at=datetime.now().isoformat(),
                )
                records.append(record)
                logger.info(
                    f"  ✓ {paper_name} | {color_text} | {size_text} | "
                    f"{FIXED_QTY}장 | ₩{price:,}"
                )
            except (StaleElementReferenceException, TimeoutException) as e:
                logger.warning(f"  [{paper_name}] 크롤링 실패: {e}")
                # 페이지 새로고침 후 재시도
                self.driver.get(url)
                time.sleep(3)
                continue
        self.results = records
        logger.info("=" * 60)
        logger.info(f"크롤링 완료: 총 {len(records)}개 용지 가격 수집")
        logger.info("=" * 60)
        return records
    def save_json(self, filepath: str = None):
        """결과를 JSON 파일로 저장"""
        import os
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if filepath is None:
            os.makedirs("output", exist_ok=True)
            filepath = f"output/ecard21_postcard_{timestamp}.json"
        data = {
            "crawled_at": datetime.now().isoformat(),
            "source": "ecard21.co.kr",
            "product": "엽서 인쇄",
            "page_url": BASE_URL + POSTCARD_PATH,
            "fixed_options": {
                "color": "양면 칼라",
                "color_code": FIXED_COLOR_CODE,
                "size": "100 x 148 mm",
                "size_index": FIXED_SIZE_INDEX,
                "quantity": int(FIXED_QTY),
            },
            "total_records": len(self.results),
            "papers": [asdict(r) for r in self.results],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"결과 저장: {filepath}")
        return filepath
# ── 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    crawler = Ecard21PostcardCrawler(headless=True)
    try:
        crawler.crawl()
        crawler.save_json()
    finally:
        crawler.close()