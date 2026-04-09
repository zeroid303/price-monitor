import requests
from bs4 import BeautifulSoup
import json
import time
BASE_URL = "https://naple.co.kr"
# 명함 카테고리 제품 목록 (코드: 제품명)
# 카드명함(303)은 사이즈 86*54mm 전용 → SIZE_CODE 별도 처리
# 고급지 디지털(304) 제외 — 192매 소량 디지털 인쇄, 가격대가 20만~38만원으로 일반 옵셋과 비교 불가
BUSINESS_CARD_PRODUCTS = {
    300: "일반지 명함 (옵셋)",
    301: "고급지 명함 (옵셋)",
    302: "엑스트라 명함 (옵셋)",
    303: "카드 명함 (옵셋)",
}
# 고정 조건
SIZE_CODE = 90050       # 90*50mm
CUT_WIDTH = 90
CUT_HEIGHT = 50
EDIT_WIDTH = 92
EDIT_HEIGHT = 52
COLOR = 6               # 양면8도
QTY = 200               # 200매 고정
ORDER_EA = 1
COATING = 0             # 후가공 없음
# 후가공 없음 기본 구조
AFTER_DEFAULT = {
    "hasAfter": 0,
    "rounding": {"rounding": 0, "size": 5},
    "osi": 0,
    "missing": 0,
    "punch": {"punch": 0, "size": 3},
    "numbering": {"numbering": 0, "start": 0, "end": 0},
    "foils": [
        {"foil": 0, "width": 0, "height": 0},
        {"foil": 0, "width": 0, "height": 0},
        {"foil": 0, "width": 0, "height": 0},
    ],
    "press": {"press": 0, "width": 0, "height": 0},
    "epoxy": {"epoxy": 0},
    "thompson": {"thompson": 0, "width": 0, "height": 0},
}
DESIGN_DEFAULT = {
    "design": 0,
    "ea": 0,
    "option": {"option": 0, "ea": 0},
}
def get_paper_options(session: requests.Session, goods_code: int) -> list[dict]:
    """제품 페이지에서 용지(paper) 옵션 목록을 파싱합니다."""
    url = f"{BASE_URL}/ko/estimate?code={goods_code}"
    resp = session.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    paper_select = soup.find("select", {"id": "paper"})
    if not paper_select:
        print(f"  [경고] code={goods_code} 페이지에서 용지 옵션을 찾을 수 없습니다.")
        return []
    options = []
    for opt in paper_select.find_all("option"):
        value = opt.get("value", "")
        text = opt.get_text(strip=True)
        if value:
            options.append({"paper_code": int(value), "paper_name": text})
    return options
def get_qty_options(session: requests.Session, goods_code: int) -> list[dict]:
    """제품 페이지에서 수량(qty) 옵션 목록을 파싱합니다."""
    url = f"{BASE_URL}/ko/estimate?code={goods_code}"
    resp = session.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    qty_select = soup.find("select", {"id": "qty"})
    if not qty_select:
        return []
    options = []
    for opt in qty_select.find_all("option"):
        value = opt.get("value", "")
        text = opt.get_text(strip=True).replace(",", "")
        if value:
            options.append({"qty_value": value, "qty_text": text})
    return options
def get_estimate(session: requests.Session, goods_code: int, paper_code: int, qty: int = QTY) -> dict | None:
    """견적 API를 호출하여 가격 정보를 가져옵니다."""
    url = f"{BASE_URL}/ko/estimate/ajax/estimate"
    # 카드명함(303)은 사이즈 86*54mm
    if goods_code == 303:
        size_code = 86054
        cut_w, cut_h = 86, 54
        edit_w, edit_h = 88, 56
    else:
        size_code = SIZE_CODE
        cut_w, cut_h = CUT_WIDTH, CUT_HEIGHT
        edit_w, edit_h = EDIT_WIDTH, EDIT_HEIGHT

    payload = {
        "goodsCode": goods_code,
        "qty": qty,
        "orderEa": ORDER_EA,
        "size": size_code,
        "cutWidth": cut_w,
        "cutHeight": cut_h,
        "editWidth": edit_w,
        "editHeight": edit_h,
        "paper": paper_code,
        "color": COLOR,
        "coating": COATING,
        "after": AFTER_DEFAULT,
        "design": DESIGN_DEFAULT,
        "countryCode": "KR",
        "unitSystem": "mm_g",
        "shippingType": "DOMESTIC",
        "destinationCountry": "",
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        resp = session.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        prices = data.get("prices", {})
        prices_fx = data.get("order", {}).get("pricesFX", {})
        print_price = prices.get("print", 0)
        vat = prices.get("vat", 0)
        delivery = prices.get("delivery", 0)
        due = prices.get("duePrice", 0)
        # 배송비 제외 금액 = 합계 - 배송비
        price_no_delivery = due - delivery
        return {
            "인쇄비": print_price,
            "후가공비": prices.get("after", 0),
            "부가세": vat,
            "배송비": delivery,
            "합계(VAT포함)": due,
            "합계(배송비제외)": price_no_delivery,
        }
    except Exception as e:
        print(f"  [에러] code={goods_code}, paper={paper_code}: {e}")
        return None
def crawl_all():
    """모든 명함 제품의 용지별 가격을 크롤링합니다."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    })
    # CSRF 토큰 추출 (메인 페이지에서)
    resp = session.get(f"{BASE_URL}/ko/estimate?code=300")
    soup = BeautifulSoup(resp.text, "html.parser")
    csrf_meta = soup.find("meta", {"name": "csrf-token"})
    if csrf_meta:
        csrf_token = csrf_meta.get("content", "")
        session.headers.update({"csrf-token": csrf_token})
        print(f"CSRF 토큰 확보: {csrf_token[:20]}...")
    else:
        print("[경고] CSRF 토큰을 찾을 수 없습니다.")
    all_results = []
    for goods_code, product_name in BUSINESS_CARD_PRODUCTS.items():
        print(f"\\n{'='*60}")
        print(f"제품: {product_name} (code={goods_code})")
        print(f"{'='*60}")
        # 용지 옵션 가져오기
        papers = get_paper_options(session, goods_code)
        if not papers:
            print("  용지 옵션이 없습니다. 건너뜁니다.")
            continue
        # 200매 옵션이 없으면 가장 가까운 수량 사용
        qty = QTY
        qty_options = get_qty_options(session, goods_code)
        if qty_options:
            qty_values = [int(o["qty_text"]) for o in qty_options]
            if QTY not in qty_values:
                closest = min(qty_values, key=lambda x: abs(x - QTY))
                print(f"  [참고] {product_name}에는 {QTY}매 옵션이 없어 {closest}매로 조회합니다.")
                qty = closest
        print(f"  용지 종류: {len(papers)}개, 수량: {qty}매")
        print(f"  {'용지명':<35} {'인쇄비':>10} {'부가세':>10} {'배송비':>10} {'합계(VAT포함)':>15}")
        print(f"  {'-'*85}")
        for paper in papers:
            estimate = get_estimate(session, goods_code, paper["paper_code"], qty)
            time.sleep(0.3)  # 서버 부담 방지
            if estimate:
                row = {
                    "제품명": product_name,
                    "제품코드": goods_code,
                    "용지명": paper["paper_name"],
                    "용지코드": paper["paper_code"],
                    "수량": qty,
                    "사이즈": "90*50",
                    "인쇄도수": "양면8도",
                    "후가공": "없음",
                    **estimate,
                }
                all_results.append(row)
                print(
                    f"  {paper['paper_name']:<35} "
                    f"{estimate['인쇄비']:>10,} "
                    f"{estimate['부가세']:>10,} "
                    f"{estimate['배송비']:>10,} "
                    f"{estimate['합계(VAT포함)']:>15,}"
                )
            else:
                try:
                    print(f"  {paper['paper_name']:<35}  -- 가격 조회 실패 --")
                except UnicodeEncodeError:
                    print(f"  paper={paper['paper_code']}  -- 가격 조회 실패 --")
    return all_results
def save_results(results: list[dict], filename: str = None):
    """결과를 JSON 파일로 저장합니다 (past/now 로테이션)."""
    if not results:
        print("\\n저장할 데이터가 없습니다.")
        return
    import shutil, os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = os.path.join(base, "output")
    os.makedirs(outdir, exist_ok=True)
    now_path = os.path.join(outdir, "naple_card_now.json")
    past_path = os.path.join(outdir, "naple_card_past.json")
    # now → past 로테이션
    if os.path.exists(now_path):
        shutil.copy2(now_path, past_path)
    # 통일 구조로 변환
    from datetime import datetime as dt
    items = []
    for r in results:
        delivery = r.get("배송비", 3000)
        total = r.get("합계(VAT포함)", 0)
        price = total - delivery
        if not price:
            continue
        items.append({
            "product": r.get("제품명", ""),
            "category": r.get("제품명", ""),
            "paper_name": r.get("용지명", ""),
            "coating": "없음",
            "color_mode": r.get("인쇄도수", "양면8도"),
            "size": r.get("사이즈", "90*50"),
            "qty": r.get("수량", 200),
            "price": price,
            "price_vat_included": True,
            "options": {"후가공": r.get("후가공", "없음")},
        })
    output = {
        "company": "naple",
        "crawled_at": dt.now().strftime("%Y-%m-%d %H:%M"),
        "items": items,
    }
    with open(now_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"JSON 저장 완료: {now_path}")
    print(f"총 {len(items)}건 저장")
if __name__ == "__main__":
    print("네이플(naple.co.kr) 명함 가격 크롤링 시작")
    print(f"조건: 사이즈 90*50 | 수량 200매 | 양면8도 | 후가공 없음")
    print(f"대상: {', '.join(BUSINESS_CARD_PRODUCTS.values())}")
    results = crawl_all()
    save_results(results)