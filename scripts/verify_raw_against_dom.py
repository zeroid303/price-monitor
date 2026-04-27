"""실측 회귀 테스트 (T1): output/{site}_{cat}_raw_now.json 의 raw 값이
실제 사이트의 셀렉터/페이지 표시값과 일치하는지 검증.

원리:
  raw item 마다 사이트 다시 방문 → 그 조합으로 셀렉터 셋팅 →
  현재 DOM 의 .mtrl-name / select text / 페이지 표시값을 다시 읽음 →
  raw 와 비교.

지원 사이트: dtpia (현재 — 추후 다른 사이트 추가 가능)

사용:
  python -m scripts.verify_raw_against_dom dtpia card_offset
  python -m scripts.verify_raw_against_dom dtpia card_digital [--limit 20]
"""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import yaml
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

ROOT = Path(__file__).resolve().parent.parent


JS_GET_MTRL_NAME = """() => {
    const el = document.querySelector('.mtrl-name');
    return el ? el.textContent.trim() : null;
}"""

JS_GET_SELECT_TEXT = """(sid) => {
    const el = document.getElementById(sid);
    if (!el || el.tagName !== 'SELECT' || el.selectedIndex < 0) return '';
    return (el.options[el.selectedIndex]?.textContent || '').trim();
}"""

JS_SET_SELECT = """({sel_id, value}) => {
    const el = document.getElementById(sel_id);
    if (!el) return false;
    el.value = String(value);
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""

JS_GET_PP_SIZE = """() => {
    const hz = document.getElementById('ppr_cut_hz')?.value;
    const vt = document.getElementById('ppr_cut_vt')?.value;
    if (!hz || !vt) return null;
    return hz + 'mm × ' + vt + 'mm';
}"""


def setup_dom_for_item(page, raw: dict, target: dict, sel: dict) -> bool:
    """raw item 의 조합대로 DOM 셋팅. 성공 여부 반환."""
    page_type = raw.get("options", {}).get("page_type")
    opts = raw.get("options", {})

    # size (PP카드는 size select 없음)
    if page_type != "mtrl_cd_only" and target.get("size_value"):
        page.evaluate(JS_SET_SELECT, {"sel_id": sel.get("size", "ppr_cut_tmp"), "value": target["size_value"]})
        page.wait_for_timeout(400)

    # paper 셋팅 (page_type 분기)
    if page_type == "page_fixed":
        # 일반명함: coating_select_value
        cv = opts.get("config_coating_select_value", "")
        page.evaluate(JS_SET_SELECT, {"sel_id": sel.get("coating_type"), "value": cv})
        page.wait_for_timeout(400)
    elif page_type == "mtrl_cd_pair":
        page.evaluate(JS_SET_SELECT, {"sel_id": sel.get("mtrl_cd"), "value": opts.get("config_mtrl_cd")})
        page.wait_for_timeout(500)
        page.evaluate(JS_SET_SELECT, {"sel_id": sel.get("mtrl_cdw"), "value": opts.get("config_mtrl_cdw")})
        page.wait_for_timeout(500)
    elif page_type == "mtrl_cd_only":
        page.evaluate(JS_SET_SELECT, {"sel_id": sel.get("mtrl_cd"), "value": opts.get("config_mtrl_cd")})
        page.wait_for_timeout(500)
    elif page_type == "mtrl_split":
        # sel_a/sel_b 는 raw options 에 보존 안 됨 → target 의 papers 에서 paper_name_out 으로 매칭
        sel_a = None; sel_b = None
        paper_value = opts.get("config_paper_value")
        weight_value = opts.get("config_weight_value")
        for p in target.get("papers", []):
            if p.get("paper_value") == paper_value and p.get("weight_value") == weight_value:
                sel_a = p.get("sel_a"); sel_b = p.get("sel_b")
                break
        if not (sel_a and sel_b):
            return False
        page.evaluate(JS_SET_SELECT, {"sel_id": sel_a, "value": paper_value})
        page.wait_for_timeout(500)
        page.evaluate(JS_SET_SELECT, {"sel_id": sel_b, "value": weight_value})
        page.wait_for_timeout(500)

    # coating (소량명함의 coating_select_value)
    if target.get("coating_select_value") is not None and sel.get("coating_type"):
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["coating_type"], "value": target["coating_select_value"]})
        page.wait_for_timeout(300)

    # color_mode
    color_value = None
    for cm in target.get("color_modes", []):
        if cm.get("name") == raw.get("print_mode"):
            color_value = cm.get("value")
            break
    if color_value:
        page.evaluate(JS_SET_SELECT, {"sel_id": sel.get("color_mode", "prn_clr_cn_gb"), "value": color_value})
        page.wait_for_timeout(300)

    # qty
    if raw.get("qty") is not None:
        page.evaluate(JS_SET_SELECT, {"sel_id": sel.get("qty", "prn_sht_cn"), "value": str(raw["qty"])})
        page.wait_for_timeout(300)

    # paper/color 변경 후 size 가 reset 되는 케이스 — 재셋팅 (PP카드 제외)
    if page_type != "mtrl_cd_only" and target.get("size_value"):
        page.evaluate(JS_SET_SELECT, {"sel_id": sel.get("size", "ppr_cut_tmp"), "value": target["size_value"]})
        page.wait_for_timeout(300)

    return True


def read_dom_actual(page, raw: dict, sel: dict) -> dict:
    page_type = raw.get("options", {}).get("page_type")
    paper_name = page.evaluate(JS_GET_MTRL_NAME)
    if isinstance(paper_name, str):
        paper_name = paper_name.strip() or None

    weight_text = None
    if page_type == "mtrl_cd_pair":
        weight_text = page.evaluate(JS_GET_SELECT_TEXT, sel.get("mtrl_cdw")) or None
    elif page_type == "mtrl_split":
        # sel_b 가 raw 에 직접 안 보이므로 mtrl_cd_02 / mtrl_02 둘 다 시도
        for cand in (sel.get("mtrl_cd_02"), sel.get("mtrl_02")):
            if cand:
                v = page.evaluate(JS_GET_SELECT_TEXT, cand)
                if v:
                    weight_text = v
                    break

    coating = page.evaluate(JS_GET_SELECT_TEXT, sel.get("coating_type", "coating_type")) or None
    print_mode = page.evaluate(JS_GET_SELECT_TEXT, sel.get("color_mode", "prn_clr_cn_gb")) or None

    if page_type == "mtrl_cd_only":
        size = page.evaluate(JS_GET_PP_SIZE)
    else:
        size = page.evaluate(JS_GET_SELECT_TEXT, sel.get("size", "ppr_cut_tmp")) or None

    qty_val = page.evaluate(
        "(sid) => document.getElementById(sid)?.value || ''",
        sel.get("qty", "prn_sht_cn"),
    )
    try: qty = int(qty_val) if qty_val else None
    except (TypeError, ValueError): qty = None

    return {
        "paper_name": paper_name,
        "paper_weight_text": weight_text,
        "coating": coating,
        "print_mode": print_mode,
        "size": size,
        "qty": qty,
    }


def verify(site: str, category: str, limit: Optional[int] = None) -> int:
    raw_path = ROOT / "output" / f"{site}_{category}_raw_now.json"
    if not raw_path.exists():
        print(f"❌ raw 없음: {raw_path}. 먼저 어댑터 실행.")
        return 1
    raw_data = json.loads(raw_path.read_text(encoding="utf-8"))
    items = raw_data.get("items", [])
    if limit:
        items = items[:limit]

    site_cfg = yaml.safe_load((ROOT / "config/sites" / f"{site}.yaml").read_text(encoding="utf-8"))
    targets = yaml.safe_load((ROOT / "config/targets" / f"{category}.yaml").read_text(encoding="utf-8"))[site]
    cat_cfg = site_cfg.get(category, {})
    sel = cat_cfg.get("selectors", {})

    # url 별로 target 매칭
    target_by_url = {t["url"]: t for t in targets}

    mismatches = []
    pass_count = 0

    print(f"검증 대상: {len(items)} items (site={site}, category={category})")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900}, locale="ko-KR",
        )
        for pat in site_cfg.get("block_patterns", []):
            context.route(pat, lambda r: r.abort())
        context.on("dialog", lambda d: d.dismiss())
        page = context.new_page()
        try:
            cur_url = None
            for i, raw in enumerate(items):
                url = raw.get("url")
                target = target_by_url.get(url)
                if not target:
                    mismatches.append({"item_idx": i, "raw": raw, "error": f"target 매칭 없음: {url}"})
                    continue
                if url != cur_url:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2000)
                        cur_url = url
                    except PwTimeout:
                        mismatches.append({"item_idx": i, "raw": raw, "error": "page goto timeout"})
                        continue
                if not setup_dom_for_item(page, raw, target, sel):
                    mismatches.append({"item_idx": i, "raw": raw, "error": "DOM 셋팅 실패"})
                    continue

                actual = read_dom_actual(page, raw, sel)

                fields = ("paper_name", "paper_weight_text", "coating", "print_mode", "size", "qty")
                diffs = {}
                for f in fields:
                    a = actual.get(f)
                    r = raw.get(f)
                    if a != r:
                        diffs[f] = {"raw": r, "actual": a}
                if diffs:
                    mismatches.append({
                        "item_idx": i,
                        "product": raw.get("product"),
                        "config_paper_name_out": raw.get("options", {}).get("config_paper_name_out"),
                        "qty": raw.get("qty"),
                        "diffs": diffs,
                    })
                else:
                    pass_count += 1

                if (i+1) % 20 == 0:
                    print(f"  ... {i+1}/{len(items)} (pass: {pass_count}, mismatches: {len(mismatches)})")
        finally:
            browser.close()

    print()
    print(f"✅ pass: {pass_count} / total: {len(items)}")
    print(f"❌ mismatches: {len(mismatches)}")
    if mismatches:
        out = ROOT / "output" / f"_verify_{site}_{category}.json"
        out.write_text(json.dumps(mismatches, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  상세: {out}")
        # 처음 10개 출력
        for m in mismatches[:10]:
            print(f"  - idx={m.get('item_idx')} product={m.get('product')} qty={m.get('qty')} err={m.get('error') or m.get('diffs')}")
    return 0 if not mismatches else 1


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser()
    p.add_argument("site")
    p.add_argument("category")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    sys.exit(verify(args.site, args.category, args.limit))


if __name__ == "__main__":
    main()
