"""프린트시티 합판전단 엑셀 → config/targets/flyer.yaml printcity 섹션 자동 생성.

입력:
  - data/printcity/flyer.xlsx

출력:
  - config/targets/flyer.yaml 의 `printcity` 섹션

엑셀 구조:
  R1: header (title, code, quantity, sales, value, calcValue)
  paper rows (MAT:): 아트-90g / 모조-80g
  size rows (SIZ:LEJ-A4 등): 6 size
  color rows (COL:40 / COL:44): 단면4도 / 양면8도
  data rows: qty (연 단위, 1연=500매), value (공급가)

필터:
  - 표준 매수: 500 / 1000 / 2000 매 (= 1연 / 2연 / 4연)
  - value=0 (미공급) 은 price: null 로 등록

target item 스키마:
  - product: "합판전단"
  - paper, paper_code
  - color_mode, color_code
  - size, size_code
  - qty (매수, 정수)
  - price (공급가)
"""
import sys
from datetime import datetime
from pathlib import Path

import yaml
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/printcity/flyer.xlsx"
DST = ROOT / "config/targets/flyer.yaml"

# 표준 매수 (매) → 연 단위 변환 (1연=500매)
TARGET_QTY_MAE = [500, 1000, 2000]   # 매수 표준
QTY_MAE_TO_YEON = {500: 0.5, 1000: 1, 2000: 2}  # 매수 → 연

# size canonical 매핑
SIZE_CODE_TO_LABEL = {
    "SIZ:LEJ-A4": "A4",
    "SIZ:LEJ-A3": "A3",
    "SIZ:LEJ-A2": "A2",
    "SIZ:LEJ-B5": "B5",
    "SIZ:LEJ-B4": "B4",
    "SIZ:LEJ-B3": "B3",
}


def parse_groups():
    """엑셀 → list[(paper, paper_code, size, size_code, color, color_code, rows)]."""
    wb = load_workbook(SRC, data_only=True)
    ws = wb["Sheet1"]
    groups = []
    cur_paper = cur_paper_code = None
    cur_size = cur_size_code = None
    cur_color = cur_color_code = None
    cur_rows = []
    for r in range(2, ws.max_row + 1):
        title = ws.cell(r, 1).value
        code = ws.cell(r, 2).value
        qty = ws.cell(r, 3).value
        sales = ws.cell(r, 4).value
        value = ws.cell(r, 5).value
        if code and isinstance(code, str):
            if code.startswith("MAT:"):
                if cur_rows:
                    groups.append((cur_paper, cur_paper_code, cur_size, cur_size_code,
                                  cur_color, cur_color_code, cur_rows))
                    cur_rows = []
                cur_paper, cur_paper_code = title, code
                continue
            if code.startswith("SIZ:"):
                if cur_rows:
                    groups.append((cur_paper, cur_paper_code, cur_size, cur_size_code,
                                  cur_color, cur_color_code, cur_rows))
                    cur_rows = []
                cur_size, cur_size_code = title, code
                continue
            if code.startswith("COL:"):
                if cur_rows:
                    groups.append((cur_paper, cur_paper_code, cur_size, cur_size_code,
                                  cur_color, cur_color_code, cur_rows))
                    cur_rows = []
                cur_color, cur_color_code = title, code
                continue
        if isinstance(qty, (int, float)) and isinstance(value, (int, float)):
            cur_rows.append((qty, sales, value))
    if cur_rows:
        groups.append((cur_paper, cur_paper_code, cur_size, cur_size_code,
                      cur_color, cur_color_code, cur_rows))
    return groups


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if not SRC.exists():
        print(f"⚠ {SRC} 없음")
        return

    groups = parse_groups()
    items = []
    for paper, paper_code, size_label, size_code, color, color_code, rows in groups:
        size_canonical = SIZE_CODE_TO_LABEL.get(size_code, size_label)
        for qty_yeon, _sales, value in rows:
            qty_mae = int(qty_yeon * 500)  # 연 → 매
            if qty_mae not in TARGET_QTY_MAE: continue
            items.append({
                "product": "합판전단",
                "paper": paper,
                "paper_code": paper_code,
                "color_mode": color,
                "color_code": color_code,
                "size": size_canonical,
                "size_code": size_code,
                "size_label": size_label,
                "qty": qty_mae,
                "qty_yeon": qty_yeon,
                "price": int(value) if value else None,  # value=0 → None
            })

    # 기존 yaml 읽고 printcity 섹션 교체 (없으면 신규)
    if DST.exists():
        data = yaml.safe_load(DST.read_text(encoding="utf-8")) or {}
    else:
        data = {}
    data["printcity"] = {
        "_description": "정적 엑셀 source — data/printcity/flyer.xlsx 기반",
        "sources": ["data/printcity/flyer.xlsx"],
        "filters_applied": {
            "qty_mae": TARGET_QTY_MAE,
            "size": list(SIZE_CODE_TO_LABEL.values()),
            "paper": ["아트지 90g", "모조지 80g"],
            "color": ["단면4도", "양면8도"],
        },
        "price_vat_included": False,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": items,
    }

    DST.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=140),
        encoding="utf-8",
    )
    print(f"✅ {DST}: printcity 섹션 갱신 — {len(items)} items")
    # 빈도 통계
    from collections import Counter
    by_qty = Counter(i["qty"] for i in items)
    print(f"  qty 분포: {dict(by_qty)}")
    null_count = sum(1 for i in items if i["price"] is None)
    print(f"  미공급 (price=null): {null_count} 건")


if __name__ == "__main__":
    main()
