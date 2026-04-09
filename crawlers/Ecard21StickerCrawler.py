# """
# 명함천국(ecard21.co.kr) 스티커 제품 크롤러
#
# - 일반 컬러 스티커: 규격 사이즈 5종 × 용지 전체 × 1,000매
# - 도무송 스티커: 대표 사이즈 5종 × 용지 전체 × 1,000매
# - Selenium 기반 (동적 가격 계산 대응)
# - 기존 명함 크롤러 구조 참조
# """
import time
import json
import logging
from datetime import datetime
from dataclasses import dataclass, asdict, field
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
# ── 스티커 제품 목록 ──────────────────────────────────────────
# 일반 컬러: 규격 사이즈 select 제공, 수량 500~10,000
# 도무송: 자유 사이즈(텍스트 입력), 수량 1,000~10,000
STICKER_PRODUCTS = [
    {
        "name": "일반 컬러 스티커",
        "path": "/product/card/fileorder/fileorder_sticker_color.asp",
        "type": "standard",       # 규격 사이즈 select 사용
    },
    {
        "name": "도무송 스티커",
        "path": "/product/card/fileorder/fileorder_sticker_cut.asp",
        "type": "domusong",        # 자유 사이즈 입력 + 모양 select
    },
]
# ── 비교 대상 사이즈 (원형 60mm만) ────────────────────────────
# 3사 비교 기준: 명함천국/오프린트미 원형 60mm, 비즈하우스 원형 65mm (사이즈 무관 동일가)
COMPARE_SIZES = [
    {"label": "원형 60mm",     "w": 60,  "h": 60,  "standard_code": None,   "domusong_shape": "pdk18"},
]
# 일반 컬러 스티커에서 사용 가능한 규격 사이즈 맵 (가로 고정 90mm)
# 규격에 없는 사이즈(50x50 등)는 "고객입력(pss99)"인데, 가격이 0원(견적문의)이므로
# 일반 컬러는 규격 매칭되는 것만 크롤링, 나머지는 도무송에서 커버
STANDARD_SIZE_MAP = {
    "60x40":   "psscf",
    "90x55":   "pss06",
    "90x60":   "pss07",
    "90x70":   "pss08",
    "90x80":   "pss09",
    "90x90":   "pss10",
    "90x100":  "pss11",
    "90x110":  "pss12",
    "90x120":  "pss13",
    "90x130":  "pss14",
    "90x140":  "pss15",
    "90x150":  "pss16",
}
FIXED_QTY = 1000  # 고정 수량
@dataclass
class StickerPriceRecord:
    """스티커 용지별 가격 레코드"""
    product_name: str       # 제품명 (일반 컬러 스티커 / 도무송 스티커)
    paper_code: str         # 용지 코드 (예: ppk28)
    paper_name: str         # 용지명 (예: 유광코팅 스티커(아트지90g))
    size_label: str         # 사이즈 라벨 (예: 명함 90x55)
    size_w: int             # 가로 (mm)
    size_h: int             # 세로 (mm)
    shape: str              # 모양 (도무송: 원형/정사각/직사각 등, 일반: "사각")
    quantity: int            # 수량
    price: int              # 가격 (원)
    crawled_at: str         # 크롤링 시각
class Ecard21StickerCrawler:
    """명함천국 스티커 제품 크롤러"""
    def __init__(self, headless: bool = True, target_papers: list[str] = None):
        """
        target_papers: 크롤링할 paper_code 목록. None이면 전체 수집.
                       예: ["ppk28", "ppk29"]
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
        self.results: list[StickerPriceRecord] = []
        self.target_papers = set(target_papers) if target_papers else None
    def close(self):
        self.driver.quit()
    # ── 유틸리티 ─────────────────────────────────────────────
    def _js_trigger_change(self, element_id: str):
        """jQuery change 이벤트 트리거 (가격 계산 함수 호출용)"""
        self.driver.execute_script(
            f"jQuery('#{element_id}').trigger('change');"
        )
    def _select_option(self, select_id: str, value: str) -> bool:
        """셀렉트 박스에서 값을 선택하고 change 이벤트 트리거"""
        try:
            select_el = self.wait.until(
                EC.presence_of_element_located((By.ID, select_id))
            )
            sel = Select(select_el)
            sel.select_by_value(value)
            self._js_trigger_change(select_id)
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.warning(f"옵션 선택 실패 [{select_id}={value}]: {e}")
            return False
    def _set_text_input(self, input_id: str, value: str):
        """텍스트 입력 필드에 값 설정 (도무송 사이즈용)"""
        try:
            el = self.driver.find_element(By.ID, input_id)
            el.clear()
            el.send_keys(value)
            # keyup + change 이벤트 트리거 (가격 재계산)
            self.driver.execute_script(
                f"jQuery('#{input_id}').trigger('keyup').trigger('change');"
            )
            return True
        except Exception as e:
            logger.warning(f"텍스트 입력 실패 [{input_id}={value}]: {e}")
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
                    })
            return options
        except NoSuchElementException:
            return []
    def _get_price(self) -> int:
        """현재 설정된 옵션에 대한 가격을 hidden input#price에서 읽기"""
        try:
            price_el = self.driver.find_element(By.ID, "price")
            val = price_el.get_attribute("value")
            return int(val) if val and val.isdigit() else 0
        except Exception:
            return 0
    def _get_paper_options(self) -> list[dict]:
        """용지 옵션 목록 수집 (중복 code 제거)"""
        options = self._get_select_options("paper_code")
        seen = set()
        unique = []
        for opt in options:
            key = (opt["value"], opt["text"])
            if key not in seen:
                seen.add(key)
                unique.append(opt)
        return unique
    # ── 일반 컬러 스티커 크롤링 ──────────────────────────────
    def _crawl_standard(self, product: dict) -> list[StickerPriceRecord]:
        """
        일반 컬러 스티커: 규격 사이즈 select 기반
        - select 구조: paper_code → color_code(단면 고정) → sizeinfo_code → qty_code
        - 고객입력 사이즈(pss99)는 가격이 0원(견적문의)이므로 제외
        - 규격 사이즈에 매칭되는 COMPARE_SIZES만 크롤링
        """
        url = BASE_URL + product["path"]
        product_name = product["name"]
        logger.info(f"▶ [{product_name}] 페이지 접속: {url}")
        self.driver.get(url)
        time.sleep(2)
        paper_options = self._get_paper_options()
        if not paper_options:
            logger.warning(f"  [{product_name}] 용지 옵션 없음 - 건너뜀")
            return []
        logger.info(f"  [{product_name}] 용지 {len(paper_options)}개 발견")
        # 규격 매칭되는 사이즈 필터링
        target_sizes = []
        for sz in COMPARE_SIZES:
            code = sz.get("standard_code")
            if code:
                target_sizes.append(sz)
            else:
                # 규격 코드가 없는 사이즈는 WxH로 매칭 시도
                key = f"{sz['w']}x{sz['h']}"
                if key in STANDARD_SIZE_MAP:
                    sz_copy = dict(sz)
                    sz_copy["standard_code"] = STANDARD_SIZE_MAP[key]
                    target_sizes.append(sz_copy)
        if not target_sizes:
            logger.info(f"  [{product_name}] 규격 매칭 사이즈 없음 - 전체 규격 사이즈 크롤링")
            # COMPARE_SIZES에 매칭되는 규격이 없으면 규격 사이즈 전체 크롤링
            avail_sizes = self._get_select_options("sizeinfo_code")
            for opt in avail_sizes:
                if opt["value"] != "pss99":  # 고객입력 제외
                    target_sizes.append({
                        "label": opt["text"],
                        "w": 0, "h": 0,
                        "standard_code": opt["value"],
                        "domusong_shape": None,
                    })
        records = []
        for paper in paper_options:
            paper_code = paper["value"]
            # target_papers 필터링
            if self.target_papers and paper_code not in self.target_papers:
                continue
            paper_name = paper["text"]
            for sz in target_sizes:
                size_code = sz.get("standard_code")
                if not size_code:
                    continue
                try:
                    # 1) 페이지 새로고침으로 상태 초기화 (용지 간 간섭 방지)
                    self.driver.get(url)
                    time.sleep(1.5)
                    # 2) 용지 선택
                    if not self._select_option("paper_code", paper_code):
                        continue
                    time.sleep(0.3)
                    # 3) 사이즈 선택
                    if not self._select_option("sizeinfo_code", size_code):
                        logger.warning(
                            f"    [{paper_name}] 사이즈 {sz['label']} 선택 실패"
                        )
                        continue
                    time.sleep(0.5)
                    # 4) 수량 1000장 선택
                    # 수량 옵션이 동적 로딩되므로 잠시 대기 후 확인
                    qty_opts = self._get_select_options("qty_code")
                    qty_value = str(FIXED_QTY)
                    qty_available = any(o["value"] == qty_value for o in qty_opts)
                    if not qty_available:
                        # 1000장이 없으면 가장 가까운 값 찾기
                        closest = None
                        min_diff = float("inf")
                        for o in qty_opts:
                            try:
                                q = int(o["value"])
                                diff = abs(q - FIXED_QTY)
                                if diff < min_diff:
                                    min_diff = diff
                                    closest = o["value"]
                            except ValueError:
                                continue
                        if closest:
                            qty_value = closest
                            logger.info(
                                f"    [{paper_name}] 1000장 없음 → {qty_value}장 사용"
                            )
                        else:
                            logger.warning(f"    [{paper_name}] 수량 옵션 없음")
                            continue
                    if not self._select_option("qty_code", qty_value):
                        continue
                    time.sleep(0.8)
                    # 5) 가격 읽기
                    price = self._get_price()
                    if price == 0:
                        logger.warning(
                            f"    [{paper_name}] {sz['label']} 가격 0원 - 건너뜀"
                        )
                        continue
                    record = StickerPriceRecord(
                        product_name=product_name,
                        paper_code=paper_code,
                        paper_name=paper_name,
                        size_label=sz["label"],
                        size_w=sz["w"],
                        size_h=sz["h"],
                        shape="사각",
                        quantity=int(qty_value),
                        price=price,
                        crawled_at=datetime.now().isoformat(),
                    )
                    records.append(record)
                    logger.info(
                        f"    ✓ {paper_name} | {sz['label']} | "
                        f"{qty_value}장 | ₩{price:,}"
                    )
                except (StaleElementReferenceException, TimeoutException) as e:
                    logger.warning(f"    [{paper_name}] {sz['label']} 실패: {e}")
                    continue
        return records
    # ── 도무송 스티커 크롤링 ─────────────────────────────────
    def _crawl_domusong(self, product: dict) -> list[StickerPriceRecord]:
        """
        도무송 스티커: 자유 사이즈 텍스트 입력 기반
        - select 구조: paper_code → color_code(단면 고정) → real_pdk(모양)
                        → sizeinfo_code(pss99 고정) → size01/size02(가로/세로 입력)
                        → qty_code
        - 모양별로 난이도(cutlevel_code)가 자동 처리됨 (원형 등 규격 모양은 숨김)
        """
        url = BASE_URL + product["path"]
        product_name = product["name"]
        logger.info(f"▶ [{product_name}] 페이지 접속: {url}")
        self.driver.get(url)
        time.sleep(2)
        paper_options = self._get_paper_options()
        if not paper_options:
            logger.warning(f"  [{product_name}] 용지 옵션 없음 - 건너뜀")
            return []
        logger.info(f"  [{product_name}] 용지 {len(paper_options)}개 발견")
        # 도무송 모양 매핑
        SHAPE_NAMES = {
            "pdk18": "원형",
            "pdk21": "정사각 라운드",
            "pdk15": "직사각 라운드",
            "pdk16": "타원형",
        }
        records = []
        for paper in paper_options:
            paper_code = paper["value"]
            # target_papers 필터링
            if self.target_papers and paper_code not in self.target_papers:
                continue
            paper_name = paper["text"]
            for sz in COMPARE_SIZES:
                shape_code = sz.get("domusong_shape")
                if not shape_code:
                    continue
                shape_name = SHAPE_NAMES.get(shape_code, shape_code)
                try:
                    # 1) 페이지 새로고침 (상태 초기화)
                    self.driver.get(url)
                    time.sleep(1.5)
                    # 2) 용지 선택
                    if not self._select_option("paper_code", paper_code):
                        continue
                    time.sleep(0.3)
                    # 3) 모양 선택 (real_pdk)
                    if not self._select_option("real_pdk", shape_code):
                        logger.warning(
                            f"    [{paper_name}] 모양 {shape_name} 선택 실패"
                        )
                        continue
                    time.sleep(0.5)
                    # 4) 사이즈 입력 (size01=가로, size02=세로)
                    self._set_text_input("size01", str(sz["w"]))
                    self._set_text_input("size02", str(sz["h"]))
                    time.sleep(0.3)
                    # 5) 수량 1000장 선택
                    qty_value = str(FIXED_QTY)
                    qty_opts = self._get_select_options("qty_code")
                    qty_available = any(o["value"] == qty_value for o in qty_opts)
                    if not qty_available:
                        closest = None
                        min_diff = float("inf")
                        for o in qty_opts:
                            try:
                                q = int(o["value"])
                                diff = abs(q - FIXED_QTY)
                                if diff < min_diff:
                                    min_diff = diff
                                    closest = o["value"]
                            except ValueError:
                                continue
                        if closest:
                            qty_value = closest
                        else:
                            logger.warning(f"    [{paper_name}] 수량 옵션 없음")
                            continue
                    if not self._select_option("qty_code", qty_value):
                        continue
                    time.sleep(0.8)
                    # 6) 가격 읽기
                    price = self._get_price()
                    if price == 0:
                        logger.warning(
                            f"    [{paper_name}] {sz['label']} 가격 0원 - 건너뜀"
                        )
                        continue
                    record = StickerPriceRecord(
                        product_name=product_name,
                        paper_code=paper_code,
                        paper_name=paper_name,
                        size_label=sz["label"],
                        size_w=sz["w"],
                        size_h=sz["h"],
                        shape=shape_name,
                        quantity=int(qty_value),
                        price=price,
                        crawled_at=datetime.now().isoformat(),
                    )
                    records.append(record)
                    logger.info(
                        f"    ✓ {paper_name} | {shape_name} {sz['label']} | "
                        f"{qty_value}장 | ₩{price:,}"
                    )
                except (StaleElementReferenceException, TimeoutException) as e:
                    logger.warning(
                        f"    [{paper_name}] {sz['label']} 실패: {e}"
                    )
                    continue
        return records
    # ── 메인 크롤링 ──────────────────────────────────────────
    def crawl_product(self, product: dict) -> list[StickerPriceRecord]:
        """제품 타입에 따라 적절한 크롤링 메서드 호출"""
        ptype = product.get("type", "standard")
        if ptype == "domusong":
            return self._crawl_domusong(product)
        else:
            return self._crawl_standard(product)
    def crawl_all(self) -> list[StickerPriceRecord]:
        """모든 스티커 제품 크롤링"""
        logger.info("=" * 60)
        logger.info("명함천국 스티커 크롤링 시작")
        logger.info(f"고정 수량: {FIXED_QTY}매")
        logger.info(f"비교 사이즈: {[s['label'] for s in COMPARE_SIZES]}")
        logger.info("=" * 60)
        all_records = []
        for product in STICKER_PRODUCTS:
            try:
                records = self.crawl_product(product)
                all_records.extend(records)
                logger.info(
                    f"  [{product['name']}] 완료: {len(records)}개 레코드 수집"
                )
            except Exception as e:
                logger.error(f"  [{product['name']}] 오류: {e}", exc_info=True)
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
            filepath = f"output/ecard21_sticker_{timestamp}.json"
        data = {
            "crawled_at": datetime.now().isoformat(),
            "source": "ecard21.co.kr",
            "category": "스티커",
            "fixed_options": {
                "color": "단면인쇄",
                "quantity": FIXED_QTY,
                "compare_sizes": [
                    {"label": s["label"], "w": s["w"], "h": s["h"]}
                    for s in COMPARE_SIZES
                ],
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
    crawler = Ecard21StickerCrawler(headless=True)
    try:
        crawler.crawl_all()
        crawler.save_json()
    finally:
        crawler.close()