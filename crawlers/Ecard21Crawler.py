"""
명함천국(ecard21.co.kr) 가격 크롤러 — 화이트리스트 기반
[설계 원칙]
- crawl_whitelist.json (매출 TOP 100) 을 직접 로드해서 크롤링 대상을 자동 생성
- 하드코딩 최소화: 용지명 → 사이트코드 매핑만 유지 (사이트 UI에서 불가피)
- JSON 교체만으로 대상 갱신 가능
[확인된 페이지 구조] (2026-03-25 실제 크롤링 검증)
- 모든 주문 페이지 공통: #paper_code, #color_code, #sizeinfo_code, #qty_code
- 커스텀 사이즈: #size01(가로), #size02(세로) — pss99 선택 시 노출
- 가격: input[name='price'] (VAT 포함, 정수)
- jQuery change 이벤트로 가격 동적 계산
- 옵션 선택 순서: 용지 → 인쇄 → 사이즈 → (커스텀입력) → 수량(반드시 마지막)
  ※ 사이즈 변경 시 수량이 100으로 리셋됨
"""
import os
import json
import time
import logging
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    JavascriptException,
    TimeoutException,
    WebDriverException,
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
BASE_URL = "https://www.ecard21.co.kr"
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1) kind_code → 주문 페이지 URL 매핑
#    사이트 카테고리 구조에서 확인한 값. 제품 종류가 추가되면 여기에 추가.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KIND_TO_PAGE = {
    # ── 명함류 ──
    "pdk01": "/product/card/fileorder/fileorder_basic.asp",      # 일반명함 (기본명함)
    "pdk02": "/product/card/fileorder/fileorder_basic.asp",      # 고품격명함 (같은 페이지, 용지로 구분)
    "pdk03": "/product/card/fileorder/fileorder_basic.asp",      # 수입지명함 (같은 페이지, 용지로 구분)
    "pdka3": "/product/card/fileorder/fileorder_spot.asp",       # 고품격부분코팅명함
    "pdk07": "/product/card/fileorder/fileorder_card.asp",       # 카드명함
    "pdk80": "/product/card/fileorder/fileorder_epoxy.asp",      # 에폭시명함
    "pdkj3": "/product/card/fileorder/fileorder_pet.asp",        # PET카드명함
    "pdk85": "/product/card/fileorder/fileorder_digital_bizcard.asp",  # 디지탈수입지명함 (전용 페이지)
    # ── 봉투 ── (동적 분기: _resolve_envelope_page 에서 처리)
    "pdk06": "__envelope__",  # 플레이스홀더 — 실제 페이지는 사이즈/인쇄로 결정
    # ── 전단지 ──
    "pdk05": "/product/card/fileorder/fileorder_jundan_basic.asp",  # 전단지
    "pdk89": "/product/card/fileorder/fileorder_jundan_basic.asp",  # 디지탈전단지 (같은 페이지)
    # ── 스티커 ──
    "pdk04": "/product/card/fileorder/fileorder_sticker_color.asp",  # 스티커
    "pdk11": "/product/card/fileorder/fileorder_sticker_cut.asp",    # 도무송스티커
    "pdk18": "/product/card/fileorder/fileorder_sticker_cut.asp",    # 원형도무송스티커
    # ── 기타 ──
    "pdk35": "/product/card/fileorder/fileorder_basic.asp",      # 엽서 (기본명함 페이지)
    "pdk39": "/product/card/fileorder/fileorder_yangsik_ncr.asp",  # NCR
    "pdk42": "/product/card/fileorder/fileorder_jundan_basic.asp",  # 카다로그 (전단지 페이지)
    "pdke4": "/product/card/fileorder/fileorder_etc_banner_1.asp",  # 배너 (실내/실외 모두 이 페이지)
    "pdkf7": None,  # 특수인쇄 — 자동 크롤링 불가
}
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# whitelist에서 제외된 항목 (crawl_whitelist.json에서 삭제됨)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [자유규격 스티커] 3건 제외 — 사이즈가 pss99(별사이즈)로 커스텀 입력이며,
#   경쟁사 비교 시 규격 사이즈 기준으로만 비교하므로 제외.
#   - rank 79: 도무송스티커 / 초강접스티커 90g / 60x51 / 1000매
#   - rank 90: 스티커 / 아트지스티커 90g / 136x96 / 1000매
#   - rank 93: 도무송스티커 / 아트지스티커 90g / 80x165 / 1000매
#
# [카다로그] 2건 제외 — 사이트에서 표지/내지 용지가 분리되어 있어 단일 용지 기준 크롤링 불가.
#   DB 확인 결과: 같은 주문에서 표지(두꺼운 용지) + 내지(얇은 용지) 2행 쌍으로 저장됨.
#     예) ef2502120293: 랑데뷰 240g(표지, 406,000원) + ef2502120294: 랑데뷰 105g(내지, 3,175,300원)
#   whitelist의 rank 63/88(랑데뷰 105g)은 내지 매출. 크롤링하려면 표지+내지 조합 가격을 합산해야 함.
#   - rank 63: 카다로그 / 랑데뷰 105g(내지) / A4 (213x300) / 500매 / 매출 635만
#   - rank 88: 카다로그 / 랑데뷰 105g(내지) / 별사이즈 (216x303) / 500매 / 매출 398만
#   - 페이지: fileorder_digital_catal.asp (소량), fileorder_adprint_catal.asp (중철), fileorder_adprint_book.asp (무선)
#
# [디지탈수입지명함 B2B 전용 용지] 6건 제외 — namee 경유 B2B 주문에서 사용되는 용지로,
#   명함천국 사이트(fileorder_digital_bizcard.asp)에서는 미판매. SQL 쿼리에서도 동일 조건 제외.
#   제외 용지: ppq07(모조), ppqm7(션샤인), ppqp9(몽블랑), ppqds(반누보화이트), ppqs7(랑데뷰), ppqc6(엑스트라누브)
#   ※ 같은 용지가 다른 카테고리(수입지명함/봉투/전단지)에서는 정상 판매 → pdk85 조합에서만 제외
#   - rank 49: 디지탈수입지명함 / 엑스트라누브 350g
#   - rank 66: 디지탈수입지명함 / 모조 220g
#   - rank 69: 디지탈수입지명함 / 션샤인 270g
#   - rank 75: 디지탈수입지명함 / 몽블랑 210g
#   - rank 80: 디지탈수입지명함 / 반누보화이트 227g
#   - rank 96: 디지탈수입지명함 / 션샤인 270g
#
# [도무송스티커 대형 사이즈] 1건 제외 — 347x237 유포지스티커는 사이트에서 견적문의(가격 0원) 처리.
#   도무송 스티커 커스텀 사이즈 중 대형은 자동 가격 계산 불가.
#   - rank 91: 도무송스티커 / 유포지스티커 80g / 347x237 / 7건 / 매출 350만
#
# [일반전단지 랑데뷰 용지] 1건 제외 — pdk05(일반전단지)인데 랑데뷰 240g(ppqs7) 용지로 기록됨.
#   일반전단지 페이지(fileorder_jundan_basic.asp)에는 랑데뷰 용지 없음 (아트90g/모조80g만 취급).
#   디지털전단지 페이지(fileorder_digital_jundan.asp)에는 랑데뷰 있으나, KIND_CODE가 pdk05(일반)라 불일치.
#   7건뿐이며 관리자 수동 주문으로 추정.
#   - rank 86: 전단지 / 랑데뷰 240g / 620x355 / 매출 361만
#
# [특수인쇄] 1건 제외 — 정형화된 옵션 없음, 자동 크롤링 불가.
#   - rank 65: 특수인쇄 / 기타 / 120x140 / 매출 442만
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2) 용지 paper_kind_code → 사이트 셀렉트 value 매핑
#    화이트리스트 JSON의 paper_kind_code를 사이트 #paper_code value로 변환.
#    사이트 드롭다운에서 확인한 값. 용지 종류가 추가되면 여기에 추가.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PAPER_KIND_TO_SITE = {
    # ── 수입지 (비코팅) ──
    "ppq04": "ppk31",   # 누브지
    "ppq05": "ppk30",   # 휘라레
    "ppqds": "ppkhr",   # 반누보화이트
    "ppq12": "ppk33",   # 머쉬멜로우
    "ppq15": "ppk34",   # 스코틀랜드
    "ppq18": "ppk38",   # 유포지
    "ppq19": "ppk37",   # 스타드림
    "ppq21": "ppk39",   # 키칼라메탈릭
    "ppq16": "ppk35",   # 그레이스
    "ppqth": "ppknm",   # 크리스탈펄
    "ppqbi": "ppkfd",   # 컨셉(블루펄)
    "ppqu2": "ppkw5",   # 아르떼
    "ppqgd": "ppkjz",   # 바우하우스 (그문드 바우하우스)
    "ppqc6": "ppkc6",   # 엑스트라누브
    "ppqc5": "ppkc5",   # 엑스트라머쉬
    "ppqc7": "ppkc7",   # 엑스트라린넨
    "ppqu6": "ppkv6",   # 엑스트라에그화이트
    "ppqu9": "ppkc8",   # 엑스트라스타 (엑스트라스타드림)
    "ppqx0": "ppkdc",   # 엑스트라띤또레또
    "ppqbl": "ppkfe",   # 엑스트라매트화이트
    # ── 일반지 (코팅/비코팅) ──
    "ppq02": "ppk16",   # 스노우화이트 (기본, 코팅 216g) — weight별로 다를 수 있음
    # ── 카드명함 ──
    "ppq27": "ppk41",   # MC카드 → 사이트: ppk41 (기본 화이트카드)
    # ── PET카드명함 ──
    "ppqfw": "ppkjr",   # PET카드지 200g 무광
    # ── 고품격명함 유광코팅 ──
    "ppqp7": "ppks7",   # 유광코팅 300g → 사이트: ppks7 (스노우화이트 300g 유광코팅)
    # ── 기타 (추가 시 여기에) ──
}
# 페이지별로 용지 코드가 다른 경우 (kind_code, paper_kind_code) → 사이트 코드
# 봉투/전단지/스티커 등은 명함 페이지와 용지 코드 체계가 완전히 다름
PAGE_PAPER_OVERRIDE = {
    # ── 봉투 (pdk06) ──
    ("pdk06", "ppq07", "pwt05"): "ppk22",   # 모조 120g
    ("pdk06", "ppq07", "pwt04"): "ppk21",   # 모조 100g → 흑백중봉투 ppk21
    ("pdk06", "ppq07", "pwt06"): "ppk23",   # 모조 150g
    ("pdk06", "ppq07", "pwt07"): "ppk24",   # 모조 180g
    ("pdk06", "ppq58", "pwt36"): "ppk76",   # 레자크체크백색G 110g
    ("pdk06", "ppq47", "pwt36"): "ppk65",   # 레자크줄백A 110g
    ("pdk06", "ppq51", "pwt21"): "ppk69",   # 크라프트 98g
    ("pdk06", "ppqs7", "pwt43"): "ppkx3",   # 랑데뷰 130g
    ("pdk06", "ppqs7", "pwt44"): "ppkx4",   # 랑데뷰 160g
    # ── 전단지 (pdk05) ──
    ("pdk05", "ppq01", "pwt04"): "ppk90",   # 아트 100g → 사이트에 90g만 있음
    ("pdk05", "ppq01", "pwt19"): "ppk90",   # 아트 90g (pwt19=90_h)
    ("pdk05", "ppq07", "pwt59"): "ppkt3",   # 모조 80g (pwt59=80_h)
    ("pdk05", "ppq02", "pwt06"): "ppk90",   # 스노우화이트 150g → 전단지에 없음, 아트 90g 대체 (note 필요)
    # ── 디지탈전단지 (pdk89) ──
    ("pdk89", "ppq02", "pwt15"): "ppk90",   # 스노우화이트 250g → 확인 필요
    # ── 스티커 (pdk04, pdk11, pdk18) ──
    # 코팅 종류별 사이트 용지 코드 매핑 (추정, 사이트 드롭다운 대조):
    #   DB coating_code → 사이트 paper_code
    #   cot01(코팅)     → ppk28   (유광코팅 스티커)
    #   cot06(무광코팅)  → ppk28_3 (무광코팅 스티커)
    #   cot02(비코팅)    → ppk28_1 (비코팅 스티커)
    #   cot05(유광코팅)  → ppk28_2 (강접 코팅 스티커)
    # ※ 현재 PAGE_PAPER_OVERRIDE는 coating_code를 키에 포함하지 않으므로,
    #   _resolve_paper에서 coating_code별 분기는 별도 처리 필요 (TODO)
    ("pdk04", "ppq06", "pwt03"): "ppk28",   # 일반스티커 아트지 90g (기본: 유광코팅)
    ("pdk11", "ppq06", "pwt03"): "ppk28",   # 도무송 아트지스티커 90g (기본: 유광코팅)
    ("pdk18", "ppq06", "pwt03"): "ppk28",   # 원형도무송 아트지스티커 90g (기본: 유광코팅)
    ("pdk11", "ppqx2", "pwt03"): "ppkdb",   # 도무송 초강접스티커 90g (ppqx2=초강접)
    ("pdk11", "ppq53", "pwt02"): "ppk71",   # 도무송 유포지스티커 80g
    # ── 디지탈수입지명함 (pdk85) — 전용 페이지는 용지 코드가 기본명함과 다름 ──
    # 페이지에 있는 용지: ppk31(누브지), ppk30(휘라레), ppk33(머쉬멜로우),
    #   ppk39(키칼라), ppk37(스타드림), ppk35(그레이스), ppk17(스노우화이트250g)
    # 페이지에 없는 용지: 엑스트라누브, 반누보화이트, 모조, 션샤인, 몽블랑 → 크롤링 불가
    # ── NCR (pdk39) ──
    ("pdk39", "ppql5", "pwt42"): "ppkn4_1",  # NCR 상하 2매 (ppql5=NCR지 50g)
    # ── 배너 (pdke4) ──
    ("pdke4", "ppqas", "pwt13"): "ppkek",   # 실내용패트배너 230g
    ("pdke4", "ppqar", "pwt13"): "ppkej",   # 실외용패트배너 230g
}
# paper_kind_code + paper_weight_code 조합으로 더 정확한 매핑이 필요한 경우
# (같은 ppq02라도 200g=ppk16, 250g비코팅=ppk17_1, 250g무광=ppk17, 300g=ppk18 등)
PAPER_WEIGHT_OVERRIDE = {
    ("ppq02", "pwt08"):  "ppk16",    # 스노우화이트 200g 코팅  → ppk16 (216g 코팅)
    ("ppq02", "pwt15"):  "ppk17_1",  # 스노우화이트 250g 비코팅 → ppk17_1
    ("ppq02", "pwt17"):  "ppk18",    # 스노우화이트 300g 무광  → ppk18
    ("ppq02", "pwt18"):  "ppkt5",    # 스노우화이트 400g 무광  → ppkt5
    ("ppq02", "pwt06"):  "ppk17_1",  # 스노우화이트 150g → 250g 비코팅으로 대체 (확인 필요)
}
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3) 사이즈 매핑 — W/H 기준으로 사이트 sizeinfo_code 결정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전단지: pss99(별사이즈) → W/H 기준으로 규격 사이즈로 매핑
FLYER_SIZE_MAP = {
    # (width, height) → 사이트 sizeinfo_code
    (300, 213): "pss17",  # A4 가로
    (213, 300): "pss17",  # A4 세로
    (297, 420): "pss18",  # A3
    (300, 423): "pss18",  # A3 (DB값 약간 다름)
    (420, 297): "pss18",  # A3 가로
    (370, 260): "pss22",  # B4 가로
    (260, 370): "pss22",  # B4 세로
    (185, 260): "pss21",  # B5
    (182, 257): "pss21",  # B5
    (203, 213): "pss17",  # A4 변형 → A4로 대체
    (478, 313): "pss18",  # 디지탈전단지 대형 → A3로 대체 (확인 필요)
}
# 도무송/원형도무송 스티커: sizeinfo_code 드롭다운 없음, W/H 직접 입력
# 이 kind_code들은 사이즈 select 스킵하고 커스텀 입력만 수행
CUSTOM_SIZE_ONLY_KINDS = {"pdk11", "pdk18"}  # 도무송, 원형도무송
# 디지탈명함: 사이즈 1종(pss02=90x50)만 있으므로 pss99 → pss02로 강제 변환
DIGITAL_CARD_SIZE_OVERRIDE = "pss02"
# 봉투: 중봉투 사이즈 코드 변환 (DB코드 → 사이트 코드)
# 봉투 페이지마다 사이즈 코드가 다름. 대봉투는 1종이라 자동 선택됨.
# 중봉투: 238x262 = 6절, 262x238 = 9절
ENVELOPE_SIZE_MAP = {
    # 컬러 중봉투
    ("envel_m", 238, 262): "pssb8",   # 6절
    ("envel_m", 262, 238): "pssb9",   # 9절
    # 흑백 중봉투
    ("envel_mblack", 238, 262): "pss34",  # 6절
    ("envel_mblack", 262, 238): "pss35",  # 9절
}
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4) 인쇄 모드 — 사이트 페이지에서 자동 선택 or 첫 번째 옵션 선택
#    whitelist에 color_code가 없으므로, 페이지의 기본값을 사용.
#    봉투: 페이지별 1종 고정 (스킵)
#    명함: 양면칼라(cld08)가 기본
#    전단지: 양면칼라(cld08) or 단면칼라(cld04) — 페이지 기본값 사용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# W/H 기반 사이즈 코드 매핑 (명함류: 가장 많이 쓰이는 사이즈 코드)
CARD_SIZE_MAP = {
    (92, 52): "pss02",
    (88, 54): "pss01",
    (52, 92): "pss02",   # 세로형 → 같은 코드
    (93, 60): "pssd3",   # 카드명함
    (94, 54): "pssm5",   # 에폭시
    (90, 58): "pss32",   # PET카드
    (92, 102): "pss99",  # 2단명함 → 별사이즈
    (87, 57): "pss99",   # 별사이즈
    (88, 56): "pss99",   # 별사이즈
    (92, 142): "pss99",  # 엽서
    (102, 150): "pss99", # 엽서
    (92, 132): "pss99",  # 엽서
}
@dataclass
class PriceRecord:
    rank: int
    product_name: str       # kind_name (예: 수입지명함)
    paper_name: str         # paper_name + paper_weight (예: 누브지 209g)
    paper_code: str         # 화이트리스트 원본 코드 (예: ppq04/pwt10)
    coating: str            # coating_name
    size: str               # WxH (예: 92x52)
    qty: int
    price: Optional[int]    # VAT 포함 가격 (원). None이면 조회 실패
    note: Optional[str]
class Ecard21WhitelistCrawler:
    """
    화이트리스트 JSON을 읽어서 각 항목의 가격을 크롤링한다.
    사용법:
        crawler = Ecard21WhitelistCrawler()
        crawler.crawl("input/crawl_whitelist.json")
        crawler.save_json("output/ecard21_whitelist_prices.json")
        crawler.close()
    """
    def __init__(self, headless: bool = True):
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
        self.results: list[PriceRecord] = []
        self._current_page: Optional[str] = None  # 현재 로드된 페이지 경로
    def close(self):
        self.driver.quit()
    # ── 페이지 제어 ──────────────────────────────────────────
    def _ensure_page(self, page_path: str):
        """필요할 때만 페이지 이동 (같은 페이지면 스킵)"""
        if self._current_page == page_path:
            return
        url = BASE_URL + page_path
        logger.info(f"  → 페이지 이동: {url}")
        self.driver.get(url)
        time.sleep(3)
        self._current_page = page_path
    def _dismiss_alert(self):
        """alert 팝업이 있으면 닫기"""
        try:
            self.driver.switch_to.alert.accept()
            time.sleep(0.3)
        except Exception:
            pass

    def _select(self, sel_id: str, value: str, delay: float = 0.8) -> bool:
        """셀렉트 값 설정 + jQuery change 트리거"""
        try:
            self.driver.execute_script(
                f"jQuery('#{sel_id}').val('{value}').change();"
            )
            time.sleep(delay)
            self._dismiss_alert()
            return True
        except JavascriptException as e:
            logger.warning(f"  셀렉트 실패 [{sel_id}={value}]: {e}")
            return False
    def _option_exists(self, sel_id: str, value: str) -> bool:
        """셀렉트 박스에 해당 옵션이 존재하는지 확인"""
        try:
            result = self.driver.execute_script(f"""
                var sel = document.getElementById('{sel_id}');
                if (!sel) return false;
                for (var i = 0; i < sel.options.length; i++) {{
                    if (sel.options[i].value === '{value}') return true;
                }}
                return false;
            """)
            return bool(result)
        except Exception:
            return False
    def _set_custom_size(self, width: int, height: int, delay: float = 0.8):
        """고객입력 사이즈 필드에 가로/세로 입력"""
        self.driver.execute_script(f"""
            var w = document.getElementById('size01');
            var h = document.getElementById('size02');
            if (w) {{ w.value = '{width}'; jQuery(w).change().blur(); }}
            if (h) {{ h.value = '{height}'; jQuery(h).change().blur(); }}
        """)
        time.sleep(delay)
    def _read_price(self) -> int:
        """input[name='price']에서 VAT 포함 가격 읽기"""
        try:
            val = self.driver.execute_script(
                'return document.querySelector("input[name=\'price\']").value;'
            )
            return int(val) if val and str(val).isdigit() else 0
        except Exception:
            return 0
    # ── 화이트리스트 항목 → 크롤링 파라미터 변환 ──────────────
    def _resolve_paper(self, item: dict) -> Optional[str]:
        """
        화이트리스트 항목에서 사이트 paper_code를 결정.
        1순위: PAGE_PAPER_OVERRIDE (kind_code + paper_kind + paper_weight 조합)
        2순위: PAPER_WEIGHT_OVERRIDE (paper_kind + paper_weight 조합)
        3순위: PAPER_KIND_TO_SITE (paper_kind만)
        """
        kk = item["kind_code"]
        pk = item["paper_kind_code"]
        pw = item["paper_weight_code"]
        # 1) 페이지별 오버라이드 (봉투/전단지/스티커 등)
        if (kk, pk, pw) in PAGE_PAPER_OVERRIDE:
            return PAGE_PAPER_OVERRIDE[(kk, pk, pw)]
        # 2) weight 오버라이드 (스노우화이트 등 같은 용지 다른 무게)
        if (pk, pw) in PAPER_WEIGHT_OVERRIDE:
            return PAPER_WEIGHT_OVERRIDE[(pk, pw)]
        # 3) kind 매핑 (명함 등 기본)
        if pk in PAPER_KIND_TO_SITE:
            return PAPER_KIND_TO_SITE[pk]
        return None
    @staticmethod
    def _resolve_envelope_page(item: dict) -> str:
        """봉투: 사이즈(대/중) × 인쇄(컬러/흑백)로 페이지 결정.
        color_code로 흑백 판단: cld01=단면흑백"""
        w, h = item.get("width", 0), item.get("height", 0)
        is_black = item.get("color_code") == "cld01"
        is_big = (w == 510 and h == 387) or (w == 387 and h == 510)
        if is_big:
            if is_black:
                return "/product/card/fileorder/fileorder_envel_bigblack.asp"
            return "/product/card/fileorder/fileorder_envel_big.asp"
        # 중봉투
        if is_black:
            return "/product/card/fileorder/fileorder_envel_mblack.asp"
        return "/product/card/fileorder/fileorder_envel_m.asp"
    # ── 단일 항목 가격 조회 ──────────────────────────────────
    def _get_price(self, item: dict) -> PriceRecord:
        """화이트리스트 항목 1건의 가격을 조회하여 PriceRecord 반환"""
        rank = item["rank"]
        kind_name = item["kind_name"]
        kind_code = item["kind_code"]
        paper_name = f"{item['paper_name']} {item['paper_weight']}g"
        paper_ref = f"{item['paper_kind_code']}/{item['paper_weight_code']}"
        coating = item["coating_name"]
        width = item.get("width", 0)
        height = item.get("height", 0)
        qty = item.get("qty")
        size_str = f"{width}x{height}"
        base = dict(
            rank=rank, product_name=kind_name, paper_name=paper_name,
            paper_code=paper_ref, coating=coating,
            size=size_str, qty=qty or 0,
        )
        # ── 크롤링 불가 체크 ──
        if qty is None:
            return PriceRecord(**base, price=None, note="수량 없음 (특수인쇄 등)")
        page_path = KIND_TO_PAGE.get(kind_code)
        if page_path is None:
            return PriceRecord(**base, price=None,
                               note=f"KIND_TO_PAGE에 '{kind_code}' 매핑 없음")
        # 봉투: 사이즈/인쇄에 따라 페이지 동적 결정
        if page_path == "__envelope__":
            page_path = self._resolve_envelope_page(item)
        site_paper = self._resolve_paper(item)
        if site_paper is None:
            return PriceRecord(**base, price=None,
                               note=f"PAPER_KIND_TO_SITE에 '{item['paper_kind_code']}' 매핑 없음")
        # ── 페이지 이동 ──
        try:
            self._ensure_page(page_path)
        except WebDriverException as e:
            return PriceRecord(**base, price=None, note=f"페이지 로드 실패: {e}")
        # ── 용지 존재 확인 ──
        if not self._option_exists("paper_code", site_paper):
            return PriceRecord(**base, price=None,
                               note=f"사이트에 용지 '{site_paper}' 없음 (페이지: {page_path})")
        # ── 옵션 선택 (순서 중요!) ──
        # 1) 용지
        if not self._select("paper_code", site_paper, delay=1.2):
            return PriceRecord(**base, price=None, note="용지 선택 실패")
        # 2) 인쇄도수 — whitelist의 color_code 사용. 봉투는 페이지별 1종 고정이라 스킵.
        color_code = item.get("color_code", "")
        if kind_code != "pdk06" and color_code:
            if self._option_exists("color_code", color_code):
                self._select("color_code", color_code, delay=0.3)
            else:
                # 정규화된 코드가 없으면 페이지 첫 번째 옵션 사용
                first_color = self.driver.execute_script("""
                    var sel = document.getElementById('color_code');
                    if (!sel) return null;
                    for (var i = 0; i < sel.options.length; i++) {
                        if (sel.options[i].value) return sel.options[i].value;
                    }
                    return null;
                """)
                if first_color:
                    self._select("color_code", first_color, delay=0.3)
        # 3) 사이즈
        if kind_code == "pdk85":
            # 디지탈수입지명함: 사이즈 1종(pss02)만 있음 → 강제 선택
            self._select("sizeinfo_code", DIGITAL_CARD_SIZE_OVERRIDE, delay=1.0)
        elif kind_code in CUSTOM_SIZE_ONLY_KINDS:
            # 도무송/원형도무송: sizeinfo_code 드롭다운 없음, W/H 직접 입력
            if width and height:
                self._set_custom_size(width, height, delay=1.0)
        elif kind_code == "pdk06":
            # 봉투: 대봉투는 사이즈 1종(자동), 중봉투는 6절/9절 선택 필요
            page_key = page_path.split("fileorder_")[-1].replace(".asp", "")
            env_key = (page_key, width, height)
            if env_key in ENVELOPE_SIZE_MAP:
                env_size = ENVELOPE_SIZE_MAP[env_key]
                self._select("sizeinfo_code", env_size, delay=1.0)
            # 대봉투는 사이즈 1종이라 선택 불필요
        else:
            # W/H 기반 사이즈 코드 결정
            actual_size = None
            # 전단지: W/H → 규격 사이즈
            if kind_code in ("pdk05", "pdk89"):
                actual_size = FLYER_SIZE_MAP.get((width, height))
            # 명함류: W/H → 사이즈 코드
            elif (width, height) in CARD_SIZE_MAP:
                actual_size = CARD_SIZE_MAP[(width, height)]
            if actual_size and self._option_exists("sizeinfo_code", actual_size):
                self._select("sizeinfo_code", actual_size, delay=1.0)
            elif actual_size == "pss99" and width and height:
                # 별사이즈 → 첫 번째 사이즈 선택 후 커스텀 입력
                self._select("sizeinfo_code", "pss99", delay=0.5)
                self._set_custom_size(width, height, delay=0.8)
            elif not actual_size:
                # 매핑 없으면 페이지 첫 번째 사이즈 선택
                first_size = self.driver.execute_script("""
                    var sel = document.getElementById('sizeinfo_code');
                    if (!sel) return null;
                    for (var i = 0; i < sel.options.length; i++) {
                        if (sel.options[i].value) return sel.options[i].value;
                    }
                    return null;
                """)
                if first_size:
                    self._select("sizeinfo_code", first_size, delay=1.0)
                else:
                    return PriceRecord(**base, price=None,
                                       note=f"사이즈 매핑 없음 ({width}x{height})")
        # 5) 수량 (반드시 마지막!)
        qty_str = str(qty)
        if not self._option_exists("qty_code", qty_str):
            # 요청 수량이 없으면 최소 수량으로 fallback
            fallback_qty = self.driver.execute_script("""
                var sel = document.getElementById('qty_code');
                if (!sel || sel.options.length < 2) return null;
                for (var i = 0; i < sel.options.length; i++) {
                    if (sel.options[i].value) return sel.options[i].value;
                }
                return null;
            """)
            if fallback_qty:
                qty_str = str(fallback_qty)
                base["qty"] = int(fallback_qty)
                logger.info(f"  수량 {qty} 없음 → {fallback_qty}으로 대체")
            else:
                return PriceRecord(**base, price=None,
                                   note=f"수량 '{qty}' 옵션 없음")
        self._select("qty_code", qty_str, delay=1.0)
        # 6) 가격 읽기
        price = self._read_price()
        if price == 0:
            return PriceRecord(**base, price=None, note="가격 0 — 옵션 조합 확인 필요")
        return PriceRecord(**base, price=price, note=None)
    # ── 전체 크롤링 ──────────────────────────────────────────
    def crawl(self, whitelist_path: str) -> list[PriceRecord]:
        """
        화이트리스트 JSON을 로드하여 전체 크롤링 실행.
        Args:
            whitelist_path: crawl_whitelist.json 경로
        """
        logger.info("=" * 60)
        logger.info("명함천국 화이트리스트 가격 크롤링 시작")
        logger.info(f"화이트리스트: {whitelist_path}")
        logger.info("=" * 60)
        with open(whitelist_path, "r", encoding="utf-8") as f:
            whitelist = json.load(f)
        items = whitelist["items"]
        logger.info(f"크롤링 대상: {len(items)}건")
        # 같은 페이지끼리 묶어서 페이지 이동 최소화
        items_sorted = sorted(items, key=lambda x: (
            KIND_TO_PAGE.get(x["kind_code"]) or "zzz",  # 같은 페이지끼리 모음
            x["rank"]
        ))
        records = []
        for i, item in enumerate(items_sorted):
            rank = item["rank"]
            kind = item["kind_name"]
            paper = f"{item['paper_name']} {item['paper_weight']}g"
            logger.info(f"[{i+1}/{len(items)}] rank {rank}: {kind} / {paper}")
            try:
                record = self._get_price(item)
                records.append(record)
                if record.price is not None:
                    logger.info(f"  ✓ ₩{record.price:,}")
                else:
                    logger.warning(f"  ✗ {record.note}")
            except Exception as e:
                logger.error(f"  ✗ 예외: {e}")
                records.append(PriceRecord(
                    rank=rank, product_name=kind, paper_name=paper,
                    paper_code=f"{item['paper_kind_code']}/{item['paper_weight_code']}",
                    coating=item.get("coating_name", ""),
                    size=f"{item.get('width', 0)}x{item.get('height', 0)}",
                    qty=item.get("qty", 0),
                    price=None, note=f"예외: {e}",
                ))
                # 예외 발생 시 페이지 상태 초기화
                self._current_page = None
        # rank 순으로 재정렬
        records.sort(key=lambda r: r.rank)
        self.results = records
        success = sum(1 for r in records if r.price is not None)
        fail = len(records) - success
        logger.info("=" * 60)
        logger.info(f"크롤링 완료: 성공 {success}건 / 실패 {fail}건 / 전체 {len(records)}건")
        logger.info("=" * 60)
        return records
    # ── JSON 저장 ────────────────────────────────────────────
    def save_json(self, filepath: str = "output/ecard21_whitelist_prices.json"):
        """
        결과를 JSON으로 저장.
        kind_name별로 그룹핑하여 products 딕셔너리에 저장.
        매 크롤링 시 crawled_at과 가격이 갱신됨.
        """
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        # kind_name별 그룹핑
        products: dict[str, list] = {}
        for record in self.results:
            pname = record.product_name
            if pname not in products:
                products[pname] = []
            products[pname].append(asdict(record))
        data = {
            "crawled_at": datetime.now().isoformat(),
            "source": "ecard21.co.kr",
            "description": "crawl_whitelist.json 기반 TOP100 옵션별 최신 가격",
            "total_records": len(self.results),
            "products": products,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"저장 완료: {filepath}")
        for kind, items in products.items():
            ok = sum(1 for it in items if it["price"] is not None)
            logger.info(f"  {kind}: {ok}/{len(items)}건")
# ── 실행 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    WHITELIST_PATH = "input/crawl_whitelist.json"
    OUTPUT_PATH = "output/ecard21_whitelist_prices.json"
    crawler = Ecard21WhitelistCrawler(headless=True)
    try:
        crawler.crawl(WHITELIST_PATH)
        crawler.save_json(OUTPUT_PATH)
    finally:
        crawler.close()