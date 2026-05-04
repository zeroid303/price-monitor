"""4 사이트 합판전단 target entry 생성 (수동 작성).

printcity 는 별도 build_printcity_flyer_targets.py 가 처리.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DST = ROOT / "config/targets/flyer.yaml"

TARGET_QTY = 2000

DTPIA = [{
    "product_name": "합판전단",
    "category": "합판전단",
    "url": "https://dtpia.co.kr/Order/Flyer/Happan.aspx",
    "page_type": "dtpia_sdiv",
    "qty_value": "0.5",
    "qty_mae": TARGET_QTY,
    "color_modes": [
        {"value": "4", "name": "단면4도"},
        {"value": "8", "name": "양면8도"},
    ],
    "sizes": [
        {"sdiv": "A", "sdiv_cd": "AA21", "size_label": "A2"},
        {"sdiv": "A", "sdiv_cd": "AA31", "size_label": "A3"},
        {"sdiv": "A", "sdiv_cd": "AA41", "size_label": "A4"},
        {"sdiv": "B", "sdiv_cd": "B041", "size_label": "B3"},
        {"sdiv": "B", "sdiv_cd": "B081", "size_label": "B4"},
    ],
    "papers": [
        {"mtrl_cd": "RAT100", "paper_name_out": "아트지 90g"},
        {"mtrl_cd": "RAT120", "paper_name_out": "아트지 120g"},
        {"mtrl_cd": "RAT150", "paper_name_out": "아트지 150g"},
        {"mtrl_cd": "RAT180", "paper_name_out": "아트지 180g"},
        {"mtrl_cd": "RMO080", "paper_name_out": "모조지 80g"},
    ],
}]

SWADPIA = [{
    "product_name": "합판전단",
    "category": "합판전단",
    "url": "https://www.swadpia.co.kr/goods/goods_view/CLF1000/GLF1001",
    "page_type": "swadpia_fside_bside",
    "qty_value": "2000",
    "qty_mae": TARGET_QTY,
    "color_modes": [
        {"name": "단면4도", "fside_color_amount": "4", "bside_color_amount": "0"},
        {"name": "양면8도", "fside_color_amount": "4", "bside_color_amount": "4"},
    ],
    "sizes": [
        {"paper_size": "A0200", "size_label": "A2"},
        {"paper_size": "A0300", "size_label": "A3"},
        {"paper_size": "A0400", "size_label": "A4"},
        {"paper_size": "B0300", "size_label": "B3"},
        {"paper_size": "B0400", "size_label": "B4"},
    ],
    "paper_combos": [
        {"paper_type": "ART", "paper_code": "ART090W00", "paper_name_out": "아트지 90g"},
        {"paper_type": "ART", "paper_code": "ART120W00", "paper_name_out": "아트지 120g"},
        {"paper_type": "ART", "paper_code": "ART150W00", "paper_name_out": "아트지 150g"},
        {"paper_type": "ART", "paper_code": "ART180W00", "paper_name_out": "아트지 180g"},
        {"paper_type": "SNW", "paper_code": "SNW120W00", "paper_name_out": "스노우지 120g"},
        {"paper_type": "SNW", "paper_code": "SNW150W00", "paper_name_out": "스노우지 150g"},
        {"paper_type": "SNW", "paper_code": "SNW180W00", "paper_name_out": "스노우지 180g"},
        {"paper_type": "VLD", "paper_code": "VLD08001E", "paper_name_out": "모조지 80g"},
        {"paper_type": "RDV", "paper_code": "RDV190N00", "paper_name_out": "랑데뷰 내츄럴 190g"},
    ],
}]

WOWPRESS = [{
    "product_name": "합판전단",
    "category": "합판전단",
    "url": "https://wowpress.co.kr/ordr/prod/dets?ProdNo=40026",
    "prod_no": "40026",
    "page_type": "wowpress_paper_tree",
    "qty_value": "4",
    "qty_mae": TARGET_QTY,
    "color_modes": [
        {"value": "255", "name": "단면 칼라4도"},
        {"value": "256", "name": "양면 칼라8도"},
    ],
    "sizes": [
        {"sizeno": "5610", "size_label": "A2"},
        {"sizeno": "5611", "size_label": "A3"},
        {"sizeno": "5612", "size_label": "A4"},
        {"sizeno": "5607", "size_label": "B3"},
        {"sizeno": "5609", "size_label": "B4"},
    ],
    "papers": [
        {"paper_no": 20690, "paper_name_out": "아트지 100g"},
        {"paper_no": 20692, "paper_name_out": "아트지 150g"},
    ],
}]

ADSLAND = [{
    "product_name": "합판전단",
    "category": "합판전단",
    "url": "https://www.adsland.com/shop/order.php?IC=IC00023",
    "ic": "IC00023",
    "page_type": "adsland_paper",
    "qty_value": "2000",
    "qty_mae": TARGET_QTY,
    "color_modes": [
        {"value": "4/0", "name": "단면4도"},
        {"value": "4/4", "name": "양면8도"},
    ],
    "sizes": [
        {"value": "A2", "size_label": "A2"},
        {"value": "A3", "size_label": "A3"},
        {"value": "A4", "size_label": "A4"},
        {"value": "B3", "size_label": "B3"},
        {"value": "B4", "size_label": "B4"},
    ],
    "papers": [
        {"paper_value": "80 모조", "paper_name_out": "모조지 80g"},
        {"paper_value": "90 아트", "paper_name_out": "아트지 90g"},
        {"paper_value": "120 아트", "paper_name_out": "아트지 120g"},
        {"paper_value": "150 아트", "paper_name_out": "아트지 150g"},
        {"paper_value": "180 아트", "paper_name_out": "아트지 180g"},
        {"paper_value": "80 스노우", "paper_name_out": "스노우지 80g"},
        {"paper_value": "120 스노우", "paper_name_out": "스노우지 120g"},
        {"paper_value": "150 스노우", "paper_name_out": "스노우지 150g"},
        {"paper_value": "180 스노우", "paper_name_out": "스노우지 180g"},
    ],
}]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    data = yaml.safe_load(DST.read_text(encoding="utf-8")) or {}
    data["dtpia"] = DTPIA
    data["swadpia"] = SWADPIA
    data["wowpress"] = WOWPRESS
    data["adsland"] = ADSLAND
    DST.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=120),
        encoding="utf-8",
    )
    print(f"✅ {DST}")
    for site in ("printcity", "dtpia", "swadpia", "wowpress", "adsland"):
        e = data.get(site)
        if isinstance(e, list):
            n_papers = sum(len(t.get("papers", t.get("paper_combos", []))) for t in e)
            n_sizes = sum(len(t.get("sizes", [])) for t in e)
            n_color = sum(len(t.get("color_modes", [])) for t in e)
            n_total = n_papers * n_sizes * n_color // (len(e) * len(e))
            print(f"  {site}: {len(e)} product, paper={n_papers} size={n_sizes} color={n_color} → 예상 ~{n_papers*n_sizes*n_color//len(e)} records")
        elif isinstance(e, dict):
            print(f"  {site}: dict ({len(e.get('items', []))} pre-expanded items)")


if __name__ == "__main__":
    main()
