"""
성원애드피아 명함 제품 크롤링
- 사이즈: 90*50 고정
- 수량: 200매 고정
- 후가공: 없음
- 인쇄도수: 양면칼라
- 프리컷팅 제외
- 용지 옵션별 가격 수집
"""
import time
import json
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
# 크롤링 대상 제품 목록 (프리컷팅 CNC7000 제외)
# ──────────────────────────────────────────────
PRODUCTS = [
    {
        "name": "일반지명함",
        "url": "https://www.swadpia.co.kr/goods/goods_view/CNC1000/GNC1001",
        "size_value": "N0100",       # 90mm*50mm
        "qty_200_available": False,   # 200매 옵션 없음 (최소 500매)
        "min_qty": "500",
        "has_coating_radio": True,    # 무광코팅/유광코팅/코팅없음 라디오
    },
    {
        "name": "고급지명함",
        "url": "https://www.swadpia.co.kr/goods/goods_view/CNC2000/GNC2001",
        "size_value": "N0100",
        "qty_200_available": True,
        "min_qty": "200",
        "has_coating_radio": False,
    },
    {
        "name": "카드명함",
        "url": "https://www.swadpia.co.kr/goods/goods_view/CNC3000/GNC3001",
        "size_value": "N0400",       # 카드명함은 90*50이 N0400
        "qty_200_available": True,
        "min_qty": "200",
        "has_coating_radio": False,
    },
    {
        "name": "하이브리드명함",
        "url": "https://www.swadpia.co.kr/goods/goods_view/CNC4000/GNC4001",
        "size_value": "N0100",
        "qty_200_available": True,
        "min_qty": "200",
        "has_coating_radio": False,
    },
    # 투명하이브리드명함 (CNC5000) → 양면칼라 옵션 없음 & 90*50 사이즈 없음 → 제외
    {
        "name": "에폭시명함",
        "url": "https://www.swadpia.co.kr/goods/goods_view/CNC6000/GNC6001",
        "size_value": "N0100",
        "qty_200_available": True,
        "min_qty": "200",
        "has_coating_radio": False,
        "default_postpress": "chk_is_epoxy",  # 기본 체크된 후가공 (해제 필요)
    },
    {
        "name": "디지털박명함",
        "url": "https://www.swadpia.co.kr/goods/goods_view/CNC8000/GNC8001",
        "size_value": "N0100",
        "qty_200_available": True,
        "min_qty": "200",
        "has_coating_radio": False,
        "default_postpress": "chk_is_dbak",  # 기본 체크된 후가공 (해제 필요)
    },
]
def create_driver():
    """Chrome WebDriver 생성"""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")          # 헤드리스 모드
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.implicitly_wait(5)
    return driver
def wait_for_price_update(driver, timeout=5):
    """가격이 업데이트될 때까지 대기"""
    time.sleep(1.5)  # JS 계산 대기
def dismiss_alert(driver):
    """alert 팝업이 있으면 닫기"""
    try:
        driver.switch_to.alert.accept()
        time.sleep(0.3)
    except Exception:
        pass

def uncheck_all_postpress(driver):
    """모든 후가공 체크박스 해제"""
    dismiss_alert(driver)
    postpress_ids = [
        "chk_is_osi",
        "chk_is_missing",
        "chk_is_bak",
        "chk_is_ap",
        "chk_is_numbering",
        "chk_is_domusong",
        "chk_is_tagong",
        "chk_is_guidori",
        "chk_is_epoxy",
        "chk_is_dbak",
    ]
    for pp_id in postpress_ids:
        try:
            dismiss_alert(driver)
            checkbox = driver.find_element(By.ID, pp_id)
            if checkbox.is_selected():
                # JavaScript로 클릭 (화면에 안 보여도 동작)
                driver.execute_script(
                    """
                    var cb = document.getElementById(arguments[0]);
                    if (cb && cb.checked) {
                        cb.click();
                    }
                    """,
                    pp_id,
                )
                time.sleep(0.5)
        except NoSuchElementException:
            continue
def get_price(driver):
    """현재 설정의 가격 정보 추출"""
    wait_for_price_update(driver)
    try:
        total_price = driver.execute_script(
            "return document.getElementById('total_price')?.value || '';"
        )
        pay_amt = driver.execute_script(
            "return document.getElementById('lbl_pay_amt')?.textContent || '';"
        )
        supply_amt = driver.execute_script(
            "return document.getElementById('lbl_supply_amt')?.textContent || '';"
        )
        tax_amt = driver.execute_script(
            "return document.getElementById('lbl_tax_amt')?.textContent || '';"
        )
    except Exception:
        total_price = ""
        pay_amt = ""
        supply_amt = ""
        tax_amt = ""
    return {
        "공급가(hidden)": total_price,
        "결제금액": pay_amt.replace("\\\\", "").replace(",", "").strip(),
        "공급가": supply_amt.replace("\\\\", "").replace(",", "").strip(),
        "부가세": tax_amt.replace("\\\\", "").replace(",", "").strip(),
    }
def set_options(driver, product):
    """
    페이지에서 공통 옵션 설정
    - 인쇄도수: 양면칼라 (CTN40)
    - 사이즈: 90*50
    - 수량: 200매 (없으면 최소 수량)
    - 후가공: 없음
    """
    wait = WebDriverWait(driver, 10)
    # 1) 인쇄도수: 양면칼라
    try:
        dismiss_alert(driver)
        print_select = Select(
            wait.until(EC.presence_of_element_located((By.ID, "print_color_type")))
        )
        print_select.select_by_value("CTN40")
        time.sleep(0.5)
        dismiss_alert(driver)
    except Exception as e:
        print(f"  [경고] 인쇄도수 설정 실패: {e}")
        return False
    # 2) 사이즈: 규격사이즈 → 90mm*50mm
    try:
        dismiss_alert(driver)
        size_type_select = Select(driver.find_element(By.ID, "size_type"))
        size_type_select.select_by_value("SZT10")  # 규격사이즈
        time.sleep(0.3)
        dismiss_alert(driver)
    except NoSuchElementException:
        pass
    try:
        dismiss_alert(driver)
        size_select = Select(driver.find_element(By.ID, "paper_size"))
        size_select.select_by_value(product["size_value"])
        time.sleep(0.5)
        dismiss_alert(driver)
    except Exception as e:
        print(f"  [경고] 사이즈 설정 실패: {e}")
        return False
    # 3) 수량: 200매 (없으면 가장 가까운 수량)
    try:
        dismiss_alert(driver)
        qty_select = Select(driver.find_element(By.ID, "paper_qty"))
        available_values = [opt.get_attribute("value") for opt in qty_select.options if opt.get_attribute("value")]
        if "200" in available_values:
            qty_select.select_by_value("200")
            product["_actual_qty"] = 200
        else:
            # 200에 가장 가까운 수량 선택
            closest = min(available_values, key=lambda x: abs(int(x) - 200))
            qty_select.select_by_value(closest)
            product["_actual_qty"] = int(closest)
            print(f"  [참고] 200매 옵션 없음 → {closest}매로 조회")
        time.sleep(0.5)
    except Exception as e:
        print(f"  [경고] 수량 설정 실패: {e}")
        return False
    # 4) 후가공: 모든 체크박스 해제
    uncheck_all_postpress(driver)
    return True
def get_paper_options(driver):
    """현재 페이지의 모든 용지 옵션 가져오기"""
    dismiss_alert(driver)
    paper_select = Select(driver.find_element(By.ID, "paper_code"))
    options = []
    for opt in paper_select.options:
        options.append({"text": opt.text, "value": opt.get_attribute("value")})
    return options
def crawl_product(driver, product):
    """단일 제품의 모든 용지별 가격 크롤링"""
    print(f"\\n{'='*60}")
    print(f"크롤링 시작: {product['name']}")
    print(f"URL: {product['url']}")
    print(f"{'='*60}")
    driver.get(product["url"])
    time.sleep(3)  # 페이지 로드 대기
    # 옵션 설정
    if not set_options(driver, product):
        print(f"  [오류] {product['name']} 옵션 설정 실패, 건너뜀")
        return []
    # 용지 목록 가져오기
    paper_options = get_paper_options(driver)
    print(f"  용지 옵션 수: {len(paper_options)}")
    results = []
    for i, paper in enumerate(paper_options):
        try:
            # 용지 선택
            dismiss_alert(driver)
            paper_select = Select(driver.find_element(By.ID, "paper_code"))
            paper_select.select_by_value(paper["value"])
            time.sleep(1)
            dismiss_alert(driver)
            # 가격 가져오기
            price_info = get_price(driver)
            actual_qty = product.get("_actual_qty", 200)
            result = {
                "제품명": product["name"],
                "용지": paper["text"],
                "용지코드": paper["value"],
                "사이즈": "90x50",
                "수량": actual_qty,
                "인쇄도수": "양면칼라",
                "후가공": "없음",
                "공급가": price_info["공급가(hidden)"],
                "결제금액(부가세포함)": price_info["결제금액"],
                "부가세": price_info["부가세"],
            }
            results.append(result)
            print(
                f"  [{i+1}/{len(paper_options)}] {paper['text']}: "
                f"공급가 {price_info['공급가(hidden)']}원, "
                f"결제금액 {price_info['결제금액']}원"
                f"{f' ({actual_qty}매 기준)' if actual_qty != 200 else ''}"
            )
        except StaleElementReferenceException:
            # DOM이 갱신된 경우 재시도
            print(f"  [{i+1}] DOM 갱신 감지, 재시도...")
            time.sleep(1)
            try:
                paper_select = Select(driver.find_element(By.ID, "paper_code"))
                paper_select.select_by_value(paper["value"])
                time.sleep(1)
                price_info = get_price(driver)
                result = {
                    "제품명": product["name"],
                    "용지": paper["text"],
                    "용지코드": paper["value"],
                    "사이즈": "90x50",
                    "수량": product.get("_actual_qty", 200),
                    "인쇄도수": "양면칼라",
                    "후가공": "없음",
                    "공급가": price_info["공급가(hidden)"],
                    "결제금액(부가세포함)": price_info["결제금액"],
                    "부가세": price_info["부가세"],
                }
                results.append(result)
            except Exception as e2:
                print(f"  [{i+1}] 재시도 실패: {e2}")
        except Exception as e:
            print(f"  [{i+1}] {paper['text']} 크롤링 실패: {e}")
    return results
def save_results(all_results, filename=None):
    """결과를 JSON으로 저장 (past/now 로테이션)"""
    import shutil, os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = os.path.join(base, "output")
    os.makedirs(outdir, exist_ok=True)
    now_path = os.path.join(outdir, "swadpia_card_now.json")
    past_path = os.path.join(outdir, "swadpia_card_past.json")
    # now → past 로테이션
    if os.path.exists(now_path):
        shutil.copy2(now_path, past_path)
    # 통일 구조로 변환
    items = []
    for r in all_results:
        try:
            price = int(str(r.get("결제금액(부가세포함)", "0")).replace(",", ""))
        except:
            continue
        items.append({
            "product": r.get("제품명", ""),
            "category": r.get("제품명", ""),
            "paper_name": r.get("용지", ""),
            "coating": "없음",
            "color_mode": r.get("인쇄도수", "양면칼라"),
            "size": r.get("사이즈", "90x50"),
            "qty": r.get("수량", 200),
            "price": price,
            "price_vat_included": True,
            "options": {"후가공": r.get("후가공", "없음")},
        })
    output = {
        "company": "swadpia",
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": items,
    }
    with open(now_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"JSON 저장 완료: {now_path}")
def main():
    print("=" * 60)
    print("성원애드피아 명함 가격 크롤링 시작")
    print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("조건: 사이즈 90x50, 수량 200매, 양면칼라, 후가공 없음")
    print("=" * 60)
    driver = create_driver()
    all_results = []
    try:
        for product in PRODUCTS:
            results = crawl_product(driver, product)
            all_results.extend(results)
            print(f"  → {product['name']}: {len(results)}개 용지 가격 수집 완료")
    except KeyboardInterrupt:
        print("\\n사용자에 의해 중단됨")
    finally:
        driver.quit()
    # 결과 저장
    if all_results:
        save_results(all_results)
        print(f"\\n총 {len(all_results)}개 용지별 가격 수집 완료")
    else:
        print("\\n수집된 데이터가 없습니다.")
    # 결과 요약 출력
    print("\\n" + "=" * 60)
    print("수집 결과 요약")
    print("=" * 60)
    for product in PRODUCTS:
        product_results = [r for r in all_results if r["제품명"] == product["name"]]
        if product_results:
            print(f"\\n[{product['name']}] - {len(product_results)}개 용지")
            for r in product_results:
                actual_qty = r.get("수량", 200)
                qty_note = f" (※{actual_qty}매 기준)" if actual_qty != 200 else ""
                print(
                    f"  {r['용지']}: 공급가 {r['공급가']}원, "
                    f"결제금액 {r['결제금액(부가세포함)']}원{qty_note}"
                )
if __name__ == "__main__":
    main()