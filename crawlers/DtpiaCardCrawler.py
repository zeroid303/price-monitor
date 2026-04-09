"""
디티피아(dtpia.co.kr) 명함 가격 크롤러
- 대상: 1~10번 명함 제품
- 조건: 수량 200매(없으면 최소수량) / 사이즈 90x50(고정 시 해당 사이즈) / 양면칼라 / 후가공 없음
- Output: output/dtpia_card_now.json
"""
import json
import os
import shutil
import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
BASE_URL = "https://dtpia.co.kr/Order/Businesscard"
# ── 제품 정의 ──────────────────────────────────────────────
PRODUCTS = [
    {
        "name": "일반명함",
        "category": "일반지명함",
        "url": f"{BASE_URL}/Color.aspx",
        "page_type": "color",          # coating_type 에 용지명 포함
        "size_select_id": "ppr_cut_tmp",
        "size_90x50_value": "CNXT",     # 90x50
    },
    {
        "name": "소량명함",
        "category": "일반지명함",
        "url": f"{BASE_URL}/SmallQuantity.aspx",
        "page_type": "small_qty",       # mtrl_cd_01 + mtrl_cd_02 + coating_type
        "size_90x50_value": "TD05",     # 90x50 (소량명함)
    },
    {
        "name": "UV옵셋명함",
        "category": "UV옵셋명함",
        "url": f"{BASE_URL}/Uv.aspx",
        "page_type": "uv",             # mtrl_cd + mtrl_cdw
        "size_90x50_value": "TUXX",     # 90x50
    },
    {
        "name": "인디고명함",
        "category": "인디고명함",
        "url": f"{BASE_URL}/indigo.aspx",
        "page_type": "indigo",          # mtrl_01 + mtrl_02
        "size_90x50_value": "TIXX",     # 90x50
    },
    {
        "name": "고급지명함",
        "category": "고급지명함",
        "url": f"{BASE_URL}/Special.aspx?code=2",
        "page_type": "special",         # mtrl_cd + mtrl_cdw
        "size_90x50_value": "TCXX",     # 90x50
    },
    {
        "name": "펄지명함",
        "category": "고급지명함",
        "url": f"{BASE_URL}/Special.aspx?code=3",
        "page_type": "special",
        "size_90x50_value": "TCXX",
    },
    {
        "name": "무늬지명함",
        "category": "고급지명함",
        "url": f"{BASE_URL}/Special.aspx?code=4",
        "page_type": "special",
        "size_90x50_value": "TCXX",
    },
    {
        "name": "두꺼운명함",
        "category": "특수명함",
        "url": f"{BASE_URL}/Extra.aspx",
        "page_type": "extra",           # mtrl_cd + mtrl_cdw
        "size_90x50_value": "TCXX",
    },
    {
        "name": "카드명함",
        "category": "특수명함",
        "url": f"{BASE_URL}/Pp.aspx",
        "page_type": "pp",             # mtrl_cd only, 사이즈 86x54 고정
        "size_90x50_value": None,
        "fixed_size": "86x54",
    },
    {
        "name": "피아노블랙박명함",
        "category": "특수명함",
        "url": f"{BASE_URL}/PianoBlack.aspx",
        "page_type": "piano_black",     # mtrl_cd_01 + mtrl_cd_02, sdiv_cd 로 사이즈
        "size_90x50_value": "ZB01",     # 90*50
    },
]
# ── 유틸 함수 ──────────────────────────────────────────────
def create_driver():
    """Chrome WebDriver 생성"""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(5)
    return driver
def wait_price_update(driver, timeout=10):
    """가격이 업데이트될 때까지 대기"""
    time.sleep(1.5)
def safe_select(driver, select_id, value):
    """드롭다운 선택 (존재하는 경우만)"""
    try:
        el = driver.find_element(By.ID, select_id)
        sel = Select(el)
        sel.select_by_value(str(value))
        time.sleep(0.3)
        return True
    except Exception:
        return False
def trigger_call_price(driver):
    """callPrice() JS 함수 호출"""
    try:
        driver.execute_script("if(typeof callPrice==='function') callPrice();")
        wait_price_update(driver)
    except Exception:
        pass
def get_price(driver):
    """현재 페이지의 가격(공급가, VAT, 합계) 읽기"""
    try:
        product_am = driver.find_element(By.ID, "est_scroll_product_am").text.strip()
        vat_am = driver.find_element(By.ID, "est_scroll_vat_am").text.strip()
        total_am = driver.find_element(By.ID, "est_scroll_total_am").text.strip()
        return {
            "product": int(product_am.replace(",", "")),
            "vat": int(vat_am.replace(",", "")),
            "total": int(total_am.replace(",", "")),
        }
    except Exception as e:
        print(f"  가격 읽기 실패: {e}")
        return None
def get_current_size(driver, product_config):
    """현재 선택된 사이즈 텍스트 반환"""
    if product_config.get("fixed_size"):
        return product_config["fixed_size"]
    sid = product_config.get("size_select_id")
    if sid:
        try:
            el = driver.find_element(By.ID, sid)
            sel = Select(el)
            text = sel.first_selected_option.text.strip()
            return text.replace("×", "x").replace("*", "x")
        except Exception:
            pass
    # fallback: ppr_cut_hz, ppr_cut_vt
    try:
        hz = driver.find_element(By.CSS_SELECTOR, "[name='ppr_cut_hz'], #ppr_cut_hz").get_attribute("value")
        vt = driver.find_element(By.CSS_SELECTOR, "[name='ppr_cut_vt'], #ppr_cut_vt").get_attribute("value")
        return f"{hz}x{vt}"
    except Exception:
        return "unknown"
# ── 용지 목록 추출 함수 (페이지 타입별) ───────────────────
def get_paper_options(driver, page_type):
    """
    페이지 타입별 용지 옵션 목록 반환.
    Returns: list of dict { "paper_name": str, "select_actions": list }
        select_actions = [{"id": select_id, "value": option_value}, ...]
    """
    papers = []
    if page_type == "color":
        # 일반명함: coating_type 옵션이 "코팅 (용지명 평량)" 형태로 paper×coating 조합
        # 모든 옵션을 별도 paper로 등록 (coating 이미 결정됨)
        el = driver.find_element(By.ID, "coating_type")
        sel = Select(el)
        for opt in sel.options:
            text = opt.text.strip()
            val = opt.get_attribute("value")
            if not val or not text:
                continue
            if "(" in text and ")" in text:
                coating = text.split("(")[0].strip() or "없음"
                pname = text.split("(")[1].split(")")[0].strip()
            else:
                coating = "없음"
                pname = text
            papers.append({
                "paper_name": pname,
                "coating": coating,
                "coating_fixed": True,  # 이 옵션이 paper×coating 조합 → 코팅 iterate 스킵
                "select_actions": [{"id": "coating_type", "value": val}],
            })
    elif page_type == "small_qty":
        # 소량명함: mtrl_cd_01(용지종류) + mtrl_cd_02(평량) + coating_type
        mtrl_01 = Select(driver.find_element(By.ID, "mtrl_cd_01"))
        for opt1 in mtrl_01.options:
            paper_base = opt1.text.strip()
            val1 = opt1.get_attribute("value")
            # 먼저 용지 선택해서 평량 옵션 갱신
            mtrl_01.select_by_value(val1)
            time.sleep(0.5)
            mtrl_02 = Select(driver.find_element(By.ID, "mtrl_cd_02"))
            for opt2 in mtrl_02.options:
                weight = opt2.text.strip()
                val2 = opt2.get_attribute("value")
                pname = f"{paper_base} {weight}"
                papers.append({
                    "paper_name": pname,
                    "coating": "없음",
                    "select_actions": [
                        {"id": "mtrl_cd_01", "value": val1},
                        {"id": "mtrl_cd_02", "value": val2},
                    ],
                })
    elif page_type == "uv":
        # UV옵셋: mtrl_cd(용지코드+평량) + mtrl_cdw
        mtrl = Select(driver.find_element(By.ID, "mtrl_cd"))
        for opt in mtrl.options:
            paper_name = opt.text.strip()
            val = opt.get_attribute("value")
            mtrl.select_by_value(val)
            time.sleep(0.3)
            # 평량
            try:
                mtrl_w = Select(driver.find_element(By.ID, "mtrl_cdw"))
                weight = mtrl_w.first_selected_option.text.strip()
                full_name = f"{paper_name} {weight}"
            except Exception:
                full_name = paper_name
            papers.append({
                "paper_name": full_name,
                "coating": "없음",
                "select_actions": [{"id": "mtrl_cd", "value": val}],
            })
    elif page_type == "indigo":
        # 인디고: mtrl_01 + mtrl_02
        mtrl_01 = Select(driver.find_element(By.ID, "mtrl_01"))
        for opt1 in mtrl_01.options:
            paper_base = opt1.text.strip()
            val1 = opt1.get_attribute("value")
            mtrl_01.select_by_value(val1)
            time.sleep(0.5)
            mtrl_02 = Select(driver.find_element(By.ID, "mtrl_02"))
            for opt2 in mtrl_02.options:
                weight = opt2.text.strip()
                val2 = opt2.get_attribute("value")
                pname = f"{paper_base} {weight}"
                papers.append({
                    "paper_name": pname,
                    "coating": "없음",
                    "select_actions": [
                        {"id": "mtrl_01", "value": val1},
                        {"id": "mtrl_02", "value": val2},
                    ],
                })
    elif page_type in ("special", "extra"):
        # 고급지/펄지/무늬지/두꺼운: mtrl_cd + mtrl_cdw
        mtrl = Select(driver.find_element(By.ID, "mtrl_cd"))
        for opt in mtrl.options:
            paper_name = opt.text.strip()
            val = opt.get_attribute("value")
            mtrl.select_by_value(val)
            time.sleep(0.3)
            try:
                mtrl_w = Select(driver.find_element(By.ID, "mtrl_cdw"))
                for wopt in mtrl_w.options:
                    weight = wopt.text.strip()
                    wval = wopt.get_attribute("value")
                    full_name = f"{paper_name} {weight}"
                    papers.append({
                        "paper_name": full_name,
                        "coating": "없음",
                        "select_actions": [
                            {"id": "mtrl_cd", "value": val},
                            {"id": "mtrl_cdw", "value": wval},
                        ],
                    })
            except Exception:
                papers.append({
                    "paper_name": paper_name,
                    "coating": "없음",
                    "select_actions": [{"id": "mtrl_cd", "value": val}],
                })
    elif page_type == "pp":
        # 카드명함(PP): mtrl_cd 만
        mtrl = Select(driver.find_element(By.ID, "mtrl_cd"))
        for opt in mtrl.options:
            paper_name = opt.text.strip()
            val = opt.get_attribute("value")
            papers.append({
                "paper_name": paper_name,
                "coating": "없음",
                "select_actions": [{"id": "mtrl_cd", "value": val}],
            })
    elif page_type == "piano_black":
        # 피아노블랙박: mtrl_cd_01 + mtrl_cd_02 (용지 1종 고정)
        mtrl_01 = Select(driver.find_element(By.ID, "mtrl_cd_01"))
        for opt1 in mtrl_01.options:
            paper_base = opt1.text.strip()
            val1 = opt1.get_attribute("value")
            mtrl_01.select_by_value(val1)
            time.sleep(0.3)
            mtrl_02 = Select(driver.find_element(By.ID, "mtrl_cd_02"))
            for opt2 in mtrl_02.options:
                weight = opt2.text.strip()
                val2 = opt2.get_attribute("value")
                pname = f"{paper_base} {weight}"
                papers.append({
                    "paper_name": pname,
                    "coating": "없음",
                    "select_actions": [
                        {"id": "mtrl_cd_01", "value": val1},
                        {"id": "mtrl_cd_02", "value": val2},
                    ],
                })
    return papers
# ── 메인 크롤링 함수 ──────────────────────────────────────
def crawl_product(driver, product_config):
    """단일 제품 페이지 크롤링 → items 리스트 반환"""
    items = []
    pname = product_config["name"]
    print(f"\\n{'='*60}")
    print(f"▶ {pname} 크롤링 시작: {product_config['url']}")
    print(f"{'='*60}")
    driver.get(product_config["url"])
    time.sleep(2)
    # 1) 사이즈 설정 (90x50 또는 고정)
    size_text = "90x50"
    if product_config.get("size_select_id") and product_config.get("size_90x50_value"):
        ok = safe_select(driver, product_config["size_select_id"], product_config["size_90x50_value"])
        if ok:
            size_text = get_current_size(driver, product_config)
            print(f"  사이즈 → {size_text}")
    elif product_config.get("fixed_size"):
        size_text = product_config["fixed_size"]
        print(f"  사이즈 (고정) → {size_text}")
    # 2) 색도: 양면칼라 (value=8)
    color_ok = safe_select(driver, "prn_clr_cn_gb", "8")
    if color_ok:
        print("  색도 → 양면칼라")
    else:
        print("  색도 선택 없음 (기본값 사용)")
    # 3) 수량: 200매 우선. 없으면 200 이상 중 가장 작은 값, 그것도 없으면 전체 최소
    qty = 200
    try:
        qty_el = driver.find_element(By.ID, "prn_sht_cn")
        qty_sel = Select(qty_el)
        qty_values = []
        for opt in qty_sel.options:
            val = opt.get_attribute("value")
            try:
                qty_values.append(int(val))
            except (TypeError, ValueError):
                continue
        if 200 in qty_values:
            qty = 200
        elif qty_values:
            above = [q for q in qty_values if q >= 200]
            qty = min(above) if above else min(qty_values)
    except Exception:
        pass
    safe_select(driver, "prn_sht_cn", str(qty))
    print(f"  수량 → {qty}매")
    trigger_call_price(driver)
    # 4) 용지 목록 추출 & 각 용지별 가격 크롤링
    paper_list = get_paper_options(driver, product_config["page_type"])
    print(f"  용지 {len(paper_list)}건 발견")
    for i, paper in enumerate(paper_list, 1):
        try:
            # 용지 옵션 선택
            for action in paper["select_actions"]:
                safe_select(driver, action["id"], action["value"])
                time.sleep(0.2)
            # 사이즈/색도/수량 재설정 (용지 변경 시 초기화될 수 있음)
            if product_config.get("size_select_id") and product_config.get("size_90x50_value"):
                safe_select(driver, product_config["size_select_id"], product_config["size_90x50_value"])
            safe_select(driver, "prn_clr_cn_gb", "8")
            safe_select(driver, "prn_sht_cn", str(qty))

            # 코팅 옵션 동적 처리
            # - paper["coating_fixed"] = True (color 페이지): 이미 coating 결정됨, iterate 안 함
            # - 그 외: coating_type 드롭다운이 있으면 모든 옵션 iterate, 없으면 단일 수집
            coating_options = []
            if paper.get("coating_fixed"):
                coating_options = [(None, paper["coating"])]
            else:
                try:
                    coat_el = driver.find_element(By.ID, "coating_type")
                    coat_sel = Select(coat_el)
                    for c in coat_sel.options:
                        cval = c.get_attribute("value")
                        ctext = c.text.strip()
                        if cval and ctext:
                            coating_options.append((cval, ctext))
                except Exception:
                    pass
                if not coating_options:
                    coating_options = [(None, "없음")]

            for cval, ctext in coating_options:
                try:
                    if cval is not None:
                        safe_select(driver, "coating_type", cval)
                        time.sleep(0.2)
                    trigger_call_price(driver)
                    price_data = get_price(driver)
                    if price_data is None:
                        print(f"  [{i}/{len(paper_list)}] {paper['paper_name']} / {ctext}: 가격 읽기 실패")
                        continue
                    actual_size = get_current_size(driver, product_config)
                    items.append({
                        "product": pname,
                        "category": product_config["category"],
                        "paper_name": paper["paper_name"],
                        "coating": ctext,
                        "color_mode": "양면칼라",
                        "size": actual_size.replace("×", "x").replace("*", "x"),
                        "qty": qty,
                        "price": price_data["total"],
                        "price_vat_included": True,
                        "options": {},
                    })
                    print(f"  [{i}/{len(paper_list)}] {paper['paper_name']} / {ctext}: {price_data['total']:,}원")
                except Exception as e:
                    print(f"  [{i}/{len(paper_list)}] {paper['paper_name']} / {ctext}: 오류 - {e}")
                    continue
        except Exception as e:
            print(f"  [{i}/{len(paper_list)}] {paper['paper_name']}: 오류 - {e}")
            continue
    return items
def main():
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    now_path = os.path.join(output_dir, "dtpia_card_now.json")
    past_path = os.path.join(output_dir, "dtpia_card_past.json")
    # now → past 로테이션
    if os.path.exists(now_path):
        shutil.copy2(now_path, past_path)
        print(f"기존 now.json → past.json 복사 완료")
    driver = create_driver()
    all_items = []
    try:
        for product_config in PRODUCTS:
            try:
                items = crawl_product(driver, product_config)
                all_items.extend(items)
                print(f"  → {product_config['name']}: {len(items)}건 수집")
            except Exception as e:
                print(f"  [ERROR] {product_config['name']} 크롤링 실패: {e}")
                continue
    finally:
        driver.quit()
    # 결과 저장
    result = {
        "company": "dtpia",
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": all_items,
    }
    with open(now_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\\n{'='*60}")
    print(f"크롤링 완료! 총 {len(all_items)}건")
    print(f"저장: {now_path}")
    print(f"{'='*60}")
if __name__ == "__main__":
    main()