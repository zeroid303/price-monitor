import requests
import json
import time
from datetime import datetime
# ══════════════════════════════════════════════════════════════════
#  프린트시티 명함 전체(16종) 가격 크롤러
#  스펙: 수량 200매, 사이즈 90x50mm, 양면칼라(양면8도), 후가공 없음
#  코팅 옵션이 있으면 코팅별로도 수집
#  출력: JSON
# ══════════════════════════════════════════════════════════════════
# 프린트시티 명함 전 제품 목록 (상품명, URL slug, API Product ID)
PRODUCTS = [
    # ── 옵셋인쇄 - 일반명함 ──
    {"name": "일반 명함",             "slug": "NameCard",                    "id": "679c60008db6e006523747ad"},
    {"name": "고급 명함",             "slug": "NameCardUnited",              "id": "679c60008db6e006523747b9"},
    {"name": "MC카드 명함",           "slug": "NameCardMC",                  "id": "679c60008db6e006523747b5"},
    {"name": "PET카드 명함",          "slug": "NameCardPet",                 "id": "679c60008db6e006523747b8"},
    {"name": "에폭시 명함",           "slug": "NameCardEpoxyemboss",         "id": "679c60008db6e006523747b2"},
    {"name": "부분코팅 명함",         "slug": "NameCardPartialCoating",      "id": "679c60008db6e006523747b6"},
    # ── 옵셋인쇄 - 카드명함 ──
    {"name": "엣지 명함",             "slug": "NameCardEdge",                "id": "679c60008db6e006523747b1"},
    # ── 옵셋인쇄 - 스페셜명함 ──
    {"name": "점자 명함",             "slug": "NameCardBraille",             "id": "679c60008db6e006523747ae"},
    # ── 디지털인쇄 - 디지털일반명함 ──
    {"name": "디지털 명함",           "slug": "DigitalNameCardGroup1",       "id": "679c57c58db6e00652374789"},
    {"name": "디지털 긴급명함",       "slug": "DigitalNameCardQuick",        "id": "679c5fff8db6e0065237478d"},
    {"name": "디지털 화이트명함",     "slug": "DigitalNameCardWhite",        "id": "679c5fff8db6e0065237478e"},
    {"name": "디지털 형광명함",       "slug": "DigitalNameCardNeon",         "id": "679c5fff8db6e0065237478c"},
    # ── 디지털인쇄 - 디지털카드명함 ──
    {"name": "디지털 카드명함",       "slug": "DigitalCardNameCardMS_V2",    "id": "679c50d38db6e0065237477e"},
    # ── 디지털인쇄 - 디지털후가공명함 ──
    {"name": "디지털 3D금박 명함",    "slug": "NameCardGold3D",              "id": "679c60008db6e006523747b3"},
    {"name": "디지털 부분에폭 명함",  "slug": "NameCardPartialEpoxy",        "id": "679c60008db6e006523747b7"},
    {"name": "디지털 홀로그램 명함",  "slug": "DigitalNameCardHologram",     "id": "679c58488db6e0065237478b"},
]
BASE_API = "https://price-api.dtp21.com/v2/productbysite"
# 90x50mm에 해당하는 사이즈 코드들 (제품마다 코드 접두어가 다름)
TARGET_SIZE_KEYWORDS = ["90X50", "90x50"]
# 양면8도에 해당하는 컬러 코드들 (제품마다 코드가 다름)
TARGET_COLOR_KEYWORDS = ["COL:44", "COL:44NE"]
# 목표 수량
TARGET_QUANTITY = 200
def fetch_product_data(product_id: str) -> dict:
    """상품 전체 데이터(모든 조합 + 가격 포함) 조회"""
    url = f"{BASE_API}/{product_id}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "OK":
        raise Exception(f"API error for {product_id}: {data}")
    return data["data"]
def is_target_size(code: str) -> bool:
    """사이즈 코드가 90x50mm인지 확인"""
    return any(kw in code.upper() for kw in TARGET_SIZE_KEYWORDS)
def is_target_color(code: str) -> bool:
    """인쇄도수 코드가 양면8도인지 확인"""
    return any(code.startswith(kw) for kw in TARGET_COLOR_KEYWORDS)
def parse_selector_group(selectors: list) -> dict:
    """셀렉터 리스트를 카테고리별로 분류"""
    result = {}
    for s in selectors:
        code = s["code"]
        prefix = code.split(":")[0]
        result[prefix] = {"code": code, "title": s["title"]}
    return result
def crawl_single_product(product_info: dict) -> dict:
    """단일 상품의 가격 정보 크롤링"""
    pid = product_info["id"]
    name = product_info["name"]
    slug = product_info["slug"]
    print(f"  크롤링 중: {name} ({slug})...")
    try:
        data = fetch_product_data(pid)
    except Exception as e:
        print(f"    ⚠ 데이터 조회 실패: {e}")
        return {
            "상품명": name,
            "URL": f"https://www.printcity.co.kr/product/{slug}",
            "오류": str(e),
        }
    product_types = data.get("productTypes", [])
    product_name_ko = data.get("productNameKO", name)
    # ── 대상 조합 필터링: 사이즈 90x50, 양면8도 ──
    matched = []
    for pt in product_types:
        sel = parse_selector_group(pt["selectors"])
        # 사이즈 확인
        siz = sel.get("SIZ")
        if siz and not is_target_size(siz["code"]):
            continue
        # 인쇄도수 확인 (양면8도)
        col = sel.get("COL")
        if col and not is_target_color(col["code"]):
            continue
        # 200매 가격 찾기 (없으면 가장 가까운 수량)
        price_entry = None
        for p in pt.get("price", []):
            if p["quantity"] == TARGET_QUANTITY:
                price_entry = p
                break
        if not price_entry and pt.get("price"):
            # 200매 없으면 가장 가까운 수량 선택
            closest = min(pt["price"], key=lambda p: abs(p["quantity"] - TARGET_QUANTITY))
            price_entry = closest
            print(f"    ⚠ {TARGET_QUANTITY}매 없음 → {closest['quantity']}매로 대체")
        matched.append({
            "selectors": sel,
            "price_200": price_entry,
        })
    # ── 코팅 옵션이 있는지 확인하고 그룹핑 ──
    has_coating = any("COT" in m["selectors"] for m in matched)
    price_entries = []
    for m in matched:
        sel = m["selectors"]
        price = m["price_200"]
        entry = {}
        # 용지 정보
        mat = sel.get("MAT")
        if mat:
            entry["용지"] = mat["title"]
            entry["용지코드"] = mat["code"]
        # 코팅 정보
        cot = sel.get("COT")
        if cot:
            entry["코팅"] = cot["title"]
            entry["코팅코드"] = cot["code"]
        # 인쇄도수 정보
        col = sel.get("COL")
        if col:
            entry["인쇄도수"] = col["title"]
            entry["인쇄도수코드"] = col["code"]
        # 사이즈 정보
        siz = sel.get("SIZ")
        if siz:
            entry["사이즈"] = siz["title"]
        # 기타 셀렉터 (박, 에폭시면 등)
        for prefix, info in sel.items():
            if prefix not in ("MAT", "COT", "COL", "SIZ"):
                entry[f"옵션_{prefix}"] = info["title"]
                entry[f"옵션_{prefix}_코드"] = info["code"]
        # 가격 정보
        if price:
            entry["수량"] = price["quantity"]
            entry["인쇄비(원)"] = price["value"]
            entry["부가세(원)"] = int(price["value"] * 0.1)
            entry["총결제액(원)"] = int(price["value"] * 1.1)
            entry["판매여부"] = price.get("sales", False)
            entry["당일판가능"] = price.get("todayTypes", {}).get("switch", False)
        else:
            entry["수량"] = TARGET_QUANTITY
            entry["인쇄비(원)"] = None
            entry["비고"] = f"{TARGET_QUANTITY}매 가격 없음"
        price_entries.append(entry)
    # ── 결과 구성 ──
    result = {
        "상품명": product_name_ko,
        "URL": f"https://www.printcity.co.kr/product/{slug}",
        "조합수": len(matched),
    }
    if has_coating:
        # 코팅별로 그룹핑
        coating_groups = {}
        for e in price_entries:
            cot_name = e.get("코팅", "코팅정보없음")
            if cot_name not in coating_groups:
                coating_groups[cot_name] = {
                    "코팅명": cot_name,
                    "코팅코드": e.get("코팅코드", ""),
                    "용지별_가격": [],
                }
            # 코팅 필드를 개별 항목에서 제거 (상위에서 표시)
            item = {k: v for k, v in e.items() if k not in ("코팅", "코팅코드")}
            coating_groups[cot_name]["용지별_가격"].append(item)
        result["코팅옵션별_가격"] = list(coating_groups.values())
    else:
        result["가격목록"] = price_entries
    return result
def crawl_all():
    """전체 명함 제품 크롤링"""
    print("=" * 60)
    print("프린트시티 명함 전 제품 가격 크롤링 시작")
    print(f"조회 조건: {TARGET_QUANTITY}매 / 90×50mm / 양면8도 / 후가공없음")
    print("=" * 60)
    output = {
        "크롤링정보": {
            "사이트": "프린트시티 (www.printcity.co.kr)",
            "크롤링일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "조회조건": {
                "수량": f"{TARGET_QUANTITY}매",
                "사이즈": "90×50mm",
                "인쇄도수": "양면8도 (양면칼라)",
                "후가공": "없음",
            },
            "가격안내": "부가세·배송비 별도 (총결제액은 부가세 포함 금액)",
            "대상제품수": len(PRODUCTS),
        },
        "제품별_가격": [],
    }
    for i, product in enumerate(PRODUCTS, 1):
        print(f"\\n[{i}/{len(PRODUCTS)}] {product['name']}")
        result = crawl_single_product(product)
        output["제품별_가격"].append(result)
        time.sleep(0.5)  # API 부하 방지
    return output
def save_results(data):
    """past/now 로테이션 후 저장"""
    import shutil, os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = os.path.join(base, "output")
    os.makedirs(outdir, exist_ok=True)
    now_path = os.path.join(outdir, "printcity_card_now.json")
    past_path = os.path.join(outdir, "printcity_card_past.json")
    if os.path.exists(now_path):
        shutil.copy2(now_path, past_path)
    # 통일 구조로 변환
    items = []
    for product in data.get("제품별_가격", []):
        pname = product.get("상품명", "")
        if "코팅옵션별_가격" in product:
            for cg in product["코팅옵션별_가격"]:
                coating = cg.get("코팅명", "없음")
                for paper in cg.get("용지별_가격", []):
                    price = paper.get("총결제액(원)")
                    if not price:
                        continue
                    items.append({
                        "product": pname,
                        "category": pname,
                        "paper_name": paper.get("용지", ""),
                        "coating": coating,
                        "color_mode": paper.get("인쇄도수", "양면8도"),
                        "size": paper.get("사이즈", "90x50"),
                        "qty": paper.get("수량", 200),
                        "price": price,
                        "price_vat_included": True,
                        "options": {},
                    })
        elif "가격목록" in product:
            for paper in product.get("가격목록", []):
                price = paper.get("총결제액(원)")
                if not price:
                    continue
                items.append({
                    "product": pname,
                    "category": pname,
                    "paper_name": paper.get("용지", ""),
                    "coating": paper.get("코팅", "없음"),
                    "color_mode": paper.get("인쇄도수", "양면8도"),
                    "size": paper.get("사이즈", "90x50"),
                    "qty": paper.get("수량", 200),
                    "price": price,
                    "price_vat_included": True,
                    "options": {},
                })
    output = {
        "company": "printcity",
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": items,
    }
    with open(now_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"JSON 저장: {now_path} ({len(items)}건)")

if __name__ == "__main__":
    data = crawl_all()
    save_results(data)
    # 이전 형식 호환용 (삭제 가능)
    filename = "printcity_all_namecard_prices.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 콘솔 요약 출력
    print("\\n" + "=" * 60)
    print("크롤링 완료! 요약:")
    print("=" * 60)
    for item in data["제품별_가격"]:
        name = item["상품명"]
        count = item.get("조합수", 0)
        if "오류" in item:
            print(f"  ❌ {name}: 오류 - {item['오류']}")
        elif "코팅옵션별_가격" in item:
            coatings = len(item["코팅옵션별_가격"])
            total_papers = sum(len(c["용지별_가격"]) for c in item["코팅옵션별_가격"])
            print(f"  ✅ {name}: 코팅 {coatings}종 × 용지 {total_papers}종 (총 {count}개 조합)")
        else:
            entries = len(item.get("가격목록", []))
            print(f"  ✅ {name}: {entries}개 가격 항목 (총 {count}개 조합)")
    print(f"\\n📄 결과 저장: {filename}")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000] + "\\n... (이하 생략)")