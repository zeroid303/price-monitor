"""
프린트시티 봉투 크롤러.
config/envelope_targets.json printcity 섹션 참조.

두 제품 (card 크롤러와 동일한 dtp21 API 구조):
  - EnvelopeOffset_ver2 (id: 679c5fff8db6e00652374798) 단면칼라 COL:40
  - EnvelopeMaster      (id: 679c5fff8db6e00652374795) 단면흑백 COL:10

API 응답 productTypes[*].selectors 예시:
  { code: "SIZ_EVST:330x245", title: "330x245" }  ← 대봉투
  { code: "SIZ_EVST:260x190", title: "260x190" }  ← 9절봉투
  { code: "MAT:MOJ-120",      title: "모조-120g" }
  { code: "COL:40",           title: "단면칼라" }
  { code: "ENP:ST",           title: "일반가공" }

필터:
  - size title ∈ {330x245, 260x190}
  - color code ∈ {COL:40, COL:10}
  - envelope processing = ENP:ST
  - quantity = 1000

가격: value × 1.1 = VAT 포함. output size는 raw title 저장 → normalize에서 canonical 변환.
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "envelope_targets.json"


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("printcity", [])


TARGETS = _load_targets()

TARGET_SIZE_TITLES = {"대봉투-규격", "9절봉투-규격", "소봉투-규격"}
TARGET_QTYS = {1000}
# ENP:ST(일반가공) + ENP:DM(도무송가공) 둘 다 수집.
# 같은 용지×사이즈 조합도 한쪽에만 1000매 가격이 있음(용지별로 다름).
TARGET_ENP_CODES = {"ENP:ST", "ENP:DM"}

BASE_API = "https://price-api.dtp21.com/v2/productbysite"
SITE_BASE_URL = "https://www.printcity.co.kr"
COMPANY = "printcity"
CATEGORY = "envelope"


def fetch_product_data(product_id: str) -> dict:
    url = f"{BASE_API}/{product_id}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "OK":
        raise Exception(f"API error for {product_id}: {data}")
    return data["data"]


def verify_url(url: str) -> bool:
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
    slug = spec["slug"]
    pname = spec["product_name"]
    pid = spec.get("product_id")
    target_color_code = spec.get("color_code")
    default_print_mode = spec.get("print_mode", "단면칼라")
    product_url = f"{SITE_BASE_URL}/product/{slug}"

    if not pid:
        print(f"  ⚠ {pname}: product_id 미지정")
        return []

    print(f"  · {pname} ({slug}) id={pid}")
    try:
        data = fetch_product_data(pid)
    except Exception as e:
        print(f"    ⚠ API 실패: {e}")
        return []

    url_ok = verify_url(product_url)
    items = []
    skipped = {"size": 0, "color": 0, "processing": 0, "qty": 0, "mat": 0}

    for pt in data.get("productTypes", []):
        sel = parse_selectors(pt["selectors"])

        # size (SIZ_EVST 또는 SIZ)
        siz = sel.get("SIZ_EVST") or sel.get("SIZ")
        if not siz or siz["title"] not in TARGET_SIZE_TITLES:
            skipped["size"] += 1
            continue
        size_title = siz["title"]

        # color
        col = sel.get("COL")
        if not col:
            skipped["color"] += 1
            continue
        col_code = col["code"]
        if target_color_code and col_code != target_color_code:
            skipped["color"] += 1
            continue
        # raw.print_mode = API 가 리턴한 COL.title (사이트 실제 표기).
        # normalize 의 aliases 가 단면4도 / 단면1도 / 칼라4도 등 다양한 표기를 canonical 로 매핑.
        col_title = (col.get("title") or "").strip()
        if col_title:
            print_mode = col_title
        elif col_code == "COL:40":
            print_mode = "단면칼라"
        elif col_code == "COL:10":
            print_mode = "단면흑백"
        else:
            print_mode = default_print_mode

        # envelope processing: 일반가공(ST) + 도무송가공(DM) 둘 다 수집.
        # 용지마다 어느 쪽에 1000매 가격이 있는지 다름.
        enp = sel.get("ENP")
        enp_code = enp["code"] if enp else ""
        if enp_code and enp_code not in TARGET_ENP_CODES:
            skipped["processing"] += 1
            continue

        # envelope type: 규격(ENT:ST) + 자켓(ENT:JK)도 있지만 자켓은 매출 타겟 외라 규격만.
        ent = sel.get("ENT")
        ent_code = ent["code"] if ent else ""
        if ent_code and ent_code != "ENT:ST":
            skipped["processing"] += 1
            continue

        # material
        mat = sel.get("MAT")
        if not mat:
            skipped["mat"] += 1
            continue

        for price in pt.get("price", []):
            qty = price.get("quantity")
            if qty not in TARGET_QTYS:
                skipped["qty"] += 1
                continue
            value = price.get("value")
            if value is None:
                continue
            items.append({
                "product":    pname,
                "category":   "봉투",
                "paper_name": (mat.get("title") or None),
                # printcity 봉투 API 는 coating 필드 없음 → null
                "coating":    None,
                "print_mode": print_mode or None,
                "size":       size_title or None,
                "qty":        qty or None,
                "price":      int(value * 1.1),
                "price_vat_included": True,
                "url":        product_url,
                "url_ok":     url_ok,
                "options": {
                    "envelope_type_code":       ent_code,
                    "envelope_processing_code": enp_code,
                    "config_default_print_mode": default_print_mode,
                },
            })

    print(f"    → {len(items)}건 (skipped: {skipped})")
    return items


def crawl_all() -> list[dict]:
    print("=" * 60)
    print(f"프린트시티 봉투 크롤링 (raw 저장, qty=1000)")
    print("=" * 60)
    if not TARGETS:
        print("⚠ 크롤 타겟 없음")
        return []
    all_items = []
    for spec in TARGETS:
        all_items.extend(crawl_product(spec))
        time.sleep(0.5)
    return all_items


def save(items: list[dict]):
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
    print(f"저장: {raw_now_path} ({len(items)}건)")


if __name__ == "__main__":
    items = crawl_all()
    save(items)
