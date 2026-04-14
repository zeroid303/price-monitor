"""
프린트시티 명함 크롤러.
화이트리스트(WHITELIST)에 명시된 (제품 × 용지 × 코팅) 조합만 크롤링.

설계 원칙:
  - output은 raw 값 그대로 저장 (coating/print_mode/size 등 사이트 표기 원문).
  - 정규화/alias 매핑은 dashboard 측에서 card_mapping_rule._normalization을 보고 적용.
  - 제품 페이지 URL을 저장 + HEAD 요청으로 생존 확인 (url_ok 필드).

조회 조건 고정:
  - size: 90×50 (raw title: "명함 90×50" 또는 유사)
  - color: 칼라 (양면8도, 단면4도) — 백색1도는 없으면 스킵
  - qty: 100, 200, 500, 1000
"""
import json
import os
import shutil
import time
from datetime import datetime

import requests


# ── 화이트리스트: 크롤링 대상 ──
# papers/coatings 값은 printcity API selectors title과 1:1 일치.
WHITELIST = [
    {
        "product_id": "679c60008db6e006523747ad",
        "product_name": "일반 명함",
        "slug": "NameCard",
        "papers": [
            "스노우화이트-250g",
            "스노우화이트-216g",
            "스노우화이트-300g",
            "스노우화이트-400g",
        ],
        "coatings": ["코팅없음", "양면무광코팅", "양면유광코팅"],
    },
    {
        "product_id": "679c60008db6e006523747b9",
        "product_name": "고급 명함",
        "slug": "NameCardUnited",
        "papers": [
            "누브지-210g",
            "반누보화이트-250g",
            "아르떼 울트라화이트-310g",
            "Extra 누브-350g",
            "휘라레-216g",
        ],
        "coatings": ["코팅없음"],
    },
    {
        "product_id": "679c60008db6e006523747b6",
        "product_name": "부분코팅 명함",
        "slug": "NameCardPartialCoating",
        "papers": ["스노우화이트-300g"],
        # 사이트 UI 표기 / API title 양쪽 허용
        "coatings": ["부분코팅-앞면", "부분UV코팅-앞면"],
    },
]

# ── 필터 조건 ──
TARGET_SIZE_KEYWORDS = ("90X50", "90x50")
TARGET_COLOR_PREFIX = "COL:"  # 모든 색도 허용 (필터는 qty/size/paper/coating로만)
TARGET_QTYS = {100, 200, 500, 1000}

BASE_API = "https://price-api.dtp21.com/v2/productbysite"
SITE_BASE_URL = "https://www.printcity.co.kr"
COMPANY = "printcity"
CATEGORY = "card"


def fetch_product_data(product_id: str) -> dict:
    url = f"{BASE_API}/{product_id}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "OK":
        raise Exception(f"API error for {product_id}: {data}")
    return data["data"]


def verify_url(url: str) -> bool:
    """제품 페이지 URL 생존 확인 (HEAD 요청, 실패 시 False)."""
    try:
        r = requests.head(url, timeout=10, allow_redirects=True)
        return 200 <= r.status_code < 400
    except Exception:
        return False


def parse_selectors(selectors: list) -> dict:
    out = {}
    for s in selectors:
        prefix = s["code"].split(":")[0]
        out[prefix] = {"code": s["code"], "title": s["title"]}
    return out


def crawl_product(spec: dict) -> list[dict]:
    pid = spec["product_id"]
    pname = spec["product_name"]
    slug = spec["slug"]
    paper_set = set(spec["papers"])
    coating_set = set(spec["coatings"])
    product_url = f"{SITE_BASE_URL}/product/{slug}"

    print(f"  · {pname} ({slug})")
    try:
        data = fetch_product_data(pid)
    except Exception as e:
        print(f"    ⚠ API 조회 실패: {e}")
        return []

    url_ok = verify_url(product_url)
    if not url_ok:
        print(f"    ⚠ 제품 URL 비정상: {product_url}")

    items = []
    skipped = {"size": 0, "color": 0, "paper": 0, "coating": 0}

    for pt in data.get("productTypes", []):
        sel = parse_selectors(pt["selectors"])

        siz = sel.get("SIZ")
        if not siz or not any(kw in siz["code"].upper() for kw in TARGET_SIZE_KEYWORDS):
            skipped["size"] += 1
            continue

        col = sel.get("COL")
        if not col:
            skipped["color"] += 1
            continue

        mat = sel.get("MAT")
        if not mat or mat["title"] not in paper_set:
            skipped["paper"] += 1
            continue

        # 코팅: COT 우선, 없으면 PCS(부분코팅) fallback
        cot = sel.get("COT") or sel.get("PCS")
        cot_title = cot["title"] if cot else "코팅없음"
        if cot_title not in coating_set:
            skipped["coating"] += 1
            continue

        # options: 매칭축 외 부가 정보. 부분코팅 여부는 PCS 필드 존재로 판단.
        options = {}
        if "PCS" in sel and "COT" not in sel:
            options["partial_coating"] = True

        for price in pt.get("price", []):
            qty = price.get("quantity")
            if qty not in TARGET_QTYS:
                continue
            value = price.get("value")
            if value is None:
                continue
            items.append({
                "product": pname,
                "category": pname,
                "paper_name": mat["title"],       # raw: "스노우화이트-250g"
                "coating": cot_title,              # raw: "양면무광코팅", "부분UV코팅-앞면"
                "print_mode": col["title"],        # raw: "양면8도", "단면4도"
                "size": siz["title"],              # raw: "명함 90×50"
                "qty": qty,
                "price": int(value * 1.1),         # 총결제액 (VAT 포함)
                "price_vat_included": True,
                "url": product_url,
                "url_ok": url_ok,
                "options": options,
            })

    print(f"    → {len(items)}건 (필터 제외: size={skipped['size']}, color={skipped['color']}, paper={skipped['paper']}, coating={skipped['coating']})")
    return items


def crawl_all() -> list[dict]:
    print("=" * 60)
    print(f"프린트시티 명함 크롤링 (raw 저장, qty {sorted(TARGET_QTYS)}, size 90x50)")
    print("=" * 60)
    all_items = []
    for spec in WHITELIST:
        all_items.extend(crawl_product(spec))
        time.sleep(0.5)
    return all_items


def save(items: list[dict]):
    """raw_now.json에 덮어쓰기만. past 로테이션은 스케줄러 책임."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = os.path.join(base, "output")
    os.makedirs(outdir, exist_ok=True)
    raw_now_path = os.path.join(outdir, f"{COMPANY}_{CATEGORY}_raw_now.json")

    output = {
        "company": COMPANY,
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": items,
    }
    with open(raw_now_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n저장: {raw_now_path} ({len(items)}건)")


if __name__ == "__main__":
    items = crawl_all()
    save(items)
