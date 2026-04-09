"""
와우프레스(wowpress) 명함 가격 크롤러
- 대상: 일반명함, 특수지명함, 디지털명함, 프리미엄명함
- 조건: 90x50, 양면 칼라8도, 후가공 없음, 200매 (없으면 최소수량)
- 출력: output/wowpress_card_now.json
"""
import json
import time
import shutil
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
)
# ──────────────────────────────────────────────
# 1. 크롤링 대상 정의
# ──────────────────────────────────────────────
CATEGORIES = [
    # (category 이름, product 이름, ProdNo)
    # 일반명함 — ProdNo 40073, 옵셋(O), 최소 500매
    {"category": "일반명함", "product": "일반명함", "prod_no": "40073"},
    # 특수지명함 — ProdNo 40070, 옵셋(O), 최소 100매
    {"category": "특수지명함", "product": "특수지명함", "prod_no": "40070"},
    # 디지털명함 — 인디고
    {"category": "디지털명함", "product": "디지털인디고명함", "prod_no": "40061"},
    # 디지털명함 — AP(구.넥스)
    {"category": "디지털명함", "product": "디지털AP명함", "prod_no": "40064"},
    # 프리미엄명함 — 고평량
    {"category": "프리미엄명함", "product": "고평량명함", "prod_no": "40066"},
    # 프리미엄명함 — 고평량에코
    {"category": "프리미엄명함", "product": "고평량에코명함", "prod_no": "40446"},
    # 프리미엄명함 — 카드명함
    {"category": "프리미엄명함", "product": "카드명함", "prod_no": "40067"},
    # 프리미엄명함 — 반투명명함
    {"category": "프리미엄명함", "product": "반투명명함", "prod_no": "40068"},
    # 프리미엄명함 — 색지명함
    {"category": "프리미엄명함", "product": "색지명함", "prod_no": "40069"},
    # 프리미엄명함 — 미니명함
    {"category": "프리미엄명함", "product": "미니명함", "prod_no": "40599"},
]
BASE_URL = "https://wowpress.co.kr/ordr/prod/dets?ProdNo={prod_no}"
TARGET_SIZE = "90x50"         # 규격
TARGET_SIZE_VALUE = "5458"    # 90x50의 SizeNo 값
TARGET_COLOR = "256"          # 양면 칼라8도
TARGET_QTY = "200"            # 200매 우선
COLOR_MODE_TEXT = "양면칼라"
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(_BASE, "output")
NOW_FILE = os.path.join(OUTPUT_DIR, "wowpress_card_now.json")
PAST_FILE = os.path.join(OUTPUT_DIR, "wowpress_card_past.json")
# ──────────────────────────────────────────────
# 2. 유틸 함수
# ──────────────────────────────────────────────
def parse_price(text: str) -> int:
    """'4,500 원' → 4500"""
    return int(text.replace(",", "").replace("원", "").replace(" ", "").strip())
def rotate_files():
    """now.json → past.json 로테이션"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if os.path.exists(NOW_FILE):
        shutil.copy2(NOW_FILE, PAST_FILE)
def safe_select(driver, select_id: str, value: str, timeout: int = 5) -> bool:
    """
    Select 요소의 값을 변경하고 onchange 이벤트를 트리거한다.
    Selenium의 Select 클래스 + JS fallback 사용.
    """
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, select_id))
        )
        # 해당 value가 옵션에 있는지 확인
        options = el.find_elements(By.TAG_NAME, "option")
        values = [o.get_attribute("value") for o in options]
        if value not in values:
            return False
        sel = Select(el)
        sel.select_by_value(value)
        # onchange 이벤트 수동 트리거 (와우프레스는 onchange로 가격 갱신)
        onchange = el.get_attribute("onchange")
        if onchange:
            driver.execute_script(f"""
                var el = document.getElementById('{select_id}');
                el.value = '{value}';
                {onchange}
            """)
        return True
    except Exception as e:
        print(f"  [WARN] safe_select({select_id}, {value}) 실패: {e}")
        return False
def wait_for_price(driver, timeout: int = 8) -> bool:
    """가격 DOM이 갱신될 때까지 대기"""
    time.sleep(1.5)  # 기본 대기
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.find_element(By.ID, "od_00_totalcost").text.strip() != ""
        )
        return True
    except TimeoutException:
        return False
def get_price_from_dom(driver) -> dict:
    """현재 DOM에서 가격 정보를 읽는다."""
    try:
        prscost_text = driver.find_element(By.ID, "od_00_prscost").text.strip()
        taxcost_text = driver.find_element(By.ID, "od_00_taxcost").text.strip()
        totalcost_text = driver.find_element(By.ID, "od_00_totalcost").text.strip()
        prscost = parse_price(prscost_text)
        taxcost = parse_price(taxcost_text)
        totalcost = parse_price(totalcost_text)
        return {
            "prscost": prscost,      # 인쇄비 (VAT별도)
            "taxcost": taxcost,       # 부가세
            "totalcost": totalcost,   # 총 결제금액 (VAT포함)
        }
    except Exception as e:
        print(f"  [ERROR] get_price_from_dom 실패: {e}")
        return None
def get_paper_list(driver) -> list:
    """
    paperList hidden input에서 용지 트리를 파싱한다.
    반환: [{paperNo, paperName, gramName, gram}, ...]
    """
    try:
        paper_list_el = driver.find_element(By.ID, "paperList")
        paper_list = json.loads(paper_list_el.get_attribute("value"))
    except Exception as e:
        print(f"  [ERROR] paperList 파싱 실패: {e}")
        return []
    # 모든 노드를 dict로 인덱싱
    node_map = {p["PaperNo"]: p for p in paper_list}
    # leaf 노드 = PGramNo > 0 (실제 평량이 있는 용지)
    leaves = []
    for p in paper_list:
        gram = p.get("PGramNo", 0)
        if gram and gram > 0:
            parent = node_map.get(p.get("PaperPNo", 0), {})
            grandparent = node_map.get(parent.get("PaperPNo", 0), {})
            # 용지명 조합: grandparent(종류) + parent(색상/서브) + gram
            paper_name_parts = []
            if grandparent.get("Name") and grandparent["Name"] != "W2.0" and grandparent["Name"] != "종이":
                paper_name_parts.append(grandparent["Name"])
            if parent.get("Name"):
                paper_name_parts.append(parent["Name"])
            paper_display = "".join(paper_name_parts) if paper_name_parts else str(p["PaperNo"])
            leaves.append({
                "paperNo": p["PaperNo"],
                "paperName": paper_display,
                "gramName": p["Name"],
                "gram": gram,
            })
    return leaves
def determine_qty(driver) -> str:
    """200매 가능하면 200, 아니면 최소 수량 반환"""
    try:
        qty_select = driver.find_element(By.ID, "spdata_00_ordqty")
        options = qty_select.find_elements(By.TAG_NAME, "option")
        values = [o.get_attribute("value") for o in options]
        if TARGET_QTY in values:
            return TARGET_QTY
        # 최소 수량 반환 (첫 번째 옵션)
        return values[0] if values else "200"
    except Exception:
        return "200"
def check_size_available(driver) -> bool:
    """90x50 사이즈가 있는지 확인"""
    try:
        size_select = driver.find_element(By.ID, "pdata_00_sizeno")
        options = size_select.find_elements(By.TAG_NAME, "option")
        values = [o.get_attribute("value") for o in options]
        return TARGET_SIZE_VALUE in values
    except Exception:
        return False
def get_size_text(driver) -> str:
    """현재 선택된 사이즈 텍스트 반환"""
    try:
        size_select = Select(driver.find_element(By.ID, "pdata_00_sizeno"))
        return size_select.first_selected_option.text.strip()
    except Exception:
        return TARGET_SIZE
def select_paper_by_no(driver, paper_no: int) -> bool:
    """
    paperNo를 기준으로 용지 셀렉트박스 3단계(종류→색상→평량)를 설정한다.
    paperList에서 부모 관계를 역추적하여 순서대로 select를 설정.
    """
    try:
        paper_list_el = driver.find_element(By.ID, "paperList")
        paper_list = json.loads(paper_list_el.get_attribute("value"))
    except Exception:
        return False
    node_map = {p["PaperNo"]: p for p in paper_list}
    target = node_map.get(paper_no)
    if not target:
        return False
    # 역추적: leaf → parent (색상/서브) → grandparent (종류)
    parent = node_map.get(target.get("PaperPNo", 0), {})
    grandparent = node_map.get(parent.get("PaperPNo", 0), {})
    # paperno3 = 종류 (반누보, 스노우지 등)
    # paperno4 = 색상/평량 (화이트, 250g 등)
    # 실제 구조: paperno3 = grandparent의 PaperNo, paperno4 = parent+leaf
    # 셀렉트 순서: paperno3(종류) → paperno4(평량/색상) → 가격 갱신
    # paperno3의 value = grandparent.PaperNo (종류)
    # paperno4의 value = target.PaperNo (leaf = 최종 용지 번호)
    # Step 1: paperno3 설정 (종류 선택) — grandparent가 종류
    paper3_value = str(grandparent.get("PaperNo", parent.get("PaperPNo", "")))
    if paper3_value:
        # paperno3 select에서 해당 value가 없으면 parent의 PaperPNo 시도
        if not safe_select(driver, "spdata_00_paperno3", paper3_value):
            paper3_value = str(parent.get("PaperPNo", ""))
            if not safe_select(driver, "spdata_00_paperno3", paper3_value):
                print(f"    [WARN] paperno3 설정 실패: {paper3_value}")
                return False
        time.sleep(1)  # 셀렉트 연쇄 갱신 대기
    # Step 2: paperno4 설정 (평량/색상 → 최종 paperNo)
    # paperno4의 옵션은 paperno3 변경 후 동적으로 갱신됨
    if not safe_select(driver, "spdata_00_paperno4", str(paper_no)):
        # parent의 PaperNo로 시도
        if not safe_select(driver, "spdata_00_paperno4", str(parent.get("PaperNo", ""))):
            print(f"    [WARN] paperno4 설정 실패: {paper_no}")
            return False
    time.sleep(1)  # 가격 갱신 대기
    return True
# ──────────────────────────────────────────────
# 3. 메인 크롤링 함수
# ──────────────────────────────────────────────
def crawl_category(driver, cat: dict) -> list:
    """
    하나의 카테고리(ProdNo)에 대해 모든 용지의 가격을 크롤링한다.
    """
    prod_no = cat["prod_no"]
    category_name = cat["category"]
    product_name = cat["product"]
    url = BASE_URL.format(prod_no=prod_no)
    print(f"\\n{'='*60}")
    print(f"[{category_name}] {product_name} (ProdNo={prod_no})")
    print(f"  URL: {url}")
    driver.get(url)
    time.sleep(3)
    items = []
    # 1. 사이즈 확인 및 설정 (90x50)
    if not check_size_available(driver):
        print(f"  [SKIP] 90x50 사이즈가 없습니다.")
        return items
    safe_select(driver, "pdata_00_sizeno", TARGET_SIZE_VALUE)
    time.sleep(1)
    # 2. 인쇄도수: 양면 칼라8도 (256) 설정
    safe_select(driver, "pdata_00_colorno", TARGET_COLOR)
    time.sleep(2)  # 용지 갱신 대기 (인쇄도수 변경 시 용지가 초기화될 수 있음)
    # 3. 수량 결정 (200매 가능하면 200, 아니면 최소수량)
    actual_qty = determine_qty(driver)
    safe_select(driver, "spdata_00_ordqty", actual_qty)
    time.sleep(1)
    # 4. 용지 목록 수집
    papers = get_paper_list(driver)
    if not papers:
        print(f"  [WARN] 용지 목록이 비어있습니다.")
        return items
    print(f"  용지 {len(papers)}종 발견, 수량={actual_qty}매")
    # 5. 각 용지별 가격 크롤링
    for idx, paper in enumerate(papers):
        paper_no = paper["paperNo"]
        paper_name = paper["paperName"]
        gram_name = paper["gramName"]
        gram = paper["gram"]
        full_name = f"{paper_name} {gram_name}" if paper_name else gram_name
        print(f"  [{idx+1}/{len(papers)}] {full_name} (paperNo={paper_no}) ...", end=" ")
        # 용지 선택
        if not select_paper_by_no(driver, paper_no):
            print("SKIP (용지 선택 실패)")
            continue
        # 수량 재설정 (용지 변경 시 수량이 초기화될 수 있음)
        actual_qty_after = determine_qty(driver)
        safe_select(driver, "spdata_00_ordqty", actual_qty_after)
        time.sleep(0.5)
        # 가격 갱신 대기
        wait_for_price(driver)
        # 가격 읽기
        price_info = get_price_from_dom(driver)
        if not price_info:
            print("SKIP (가격 읽기 실패)")
            continue
        size_text = get_size_text(driver)
        item = {
            "product": product_name,
            "category": category_name,
            "paper_name": f"{paper_name} {gram_name}".strip(),
            "coating": "없음",
            "color_mode": COLOR_MODE_TEXT,
            "size": size_text.replace("x", "x"),  # 90x50
            "qty": int(actual_qty_after),
            "price": price_info["totalcost"],       # VAT포함 총액
            "price_vat_included": True,
            "options": {
                "side": "양면",
                "corner": "없음",
            },
        }
        items.append(item)
        print(f"✓ {price_info['totalcost']}원 (VAT포함)")
    return items
def main():
    print("=" * 60)
    print("와우프레스 명함 가격 크롤러 시작")
    print("=" * 60)
    # now → past 로테이션
    rotate_files()
    # Selenium 드라이버 설정
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")            # 헤드리스 모드
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.implicitly_wait(5)
    all_items = []
    try:
        for cat in CATEGORIES:
            try:
                items = crawl_category(driver, cat)
                all_items.extend(items)
            except Exception as e:
                print(f"  [ERROR] {cat['product']} 크롤링 실패: {e}")
                continue
    finally:
        driver.quit()
    # ──────────────────────────────────────────
    # JSON 출력 저장
    # ──────────────────────────────────────────
    crawled_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    output = {
        "company": "wowpress",
        "crawled_at": crawled_at,
        "items": all_items,
    }
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(NOW_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\\n{'='*60}")
    print(f"크롤링 완료: {len(all_items)}개 항목")
    print(f"저장: {NOW_FILE}")
    print(f"{'='*60}")
if __name__ == "__main__":
    main()