"""와우프레스 사각형 스티커 어댑터.

페이지: linkProdNo=40133 카테고리의 13 하위 제품 (config/targets/sticker.yaml wowpress 섹션).
각 제품 dets 페이지 (/ordr/prod/dets?ProdNo=NNNNN) 별도 진입.

봉투(40034) 와 동일 패턴 + 사이즈 처리 동적:
- paperno3/4/5 cascade (paperList JSON 기반 leaf paper_no 정확 매칭)
- 사이즈: pdata_00_sizeno select 옵션 mm 파싱 → 표준 8 사이즈 ±5mm 매칭
  (select 없으면 width/height input 으로 직접 입력)
- ColorNo / qty=1000 / ord_cnt=1 / fnOrdSummary() 가격 트리거
- 가격: totalcost - taxcost (공급가)

미공급 paper × size 는 paperno5/sizeno dropdown 에 없으면 skip.
소량 시리즈는 1000매 미공급으로 자동 skip.
"""
import re
import time
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from adapters._wowpress_card_common import (
    JS_AVAIL_OPTIONS, JS_GET_PRICE_PAIR, JS_GET_SELECT_TEXT, JS_GET_SELECT_VALUE,
    JS_PAPER_PARENT_CHAIN,
    init_browser, js_set_select, js_get_select_text, parse_int_price,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


CANONICAL_SIZES = [
    (60, 40), (80, 50), (90, 55), (90, 60),
    (90, 70), (90, 80), (90, 100), (90, 120),
]
SIZE_TOL_MM = 5

DEFAULT_QTY = "1000"


JS_DUMP_OPTS = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return [];
    return [...el.options].map(o => ({value: o.value, text: (o.textContent||'').trim()}));
}"""


def _parse_size(text: str):
    m = re.search(r"(\d+)\s*[x×*X]\s*(\d+)", text)
    if not m: return None
    return (int(m.group(1)), int(m.group(2)))


def _near_canonical(w: int, h: int, tol: int = SIZE_TOL_MM):
    for tw, th in CANONICAL_SIZES:
        if w == tw and h == th: return (tw, th)
    for tw, th in CANONICAL_SIZES:
        if abs(w - tw) <= tol and abs(h - th) <= tol: return (tw, th)
    return None


def _read_supply_price(page, sel: dict) -> Optional[int]:
    pair = page.evaluate(
        JS_GET_PRICE_PAIR,
        {"total_id": sel["price_total"], "tax_id": sel["price_tax"]},
    ) or {}
    total = parse_int_price(pair.get("total"))
    tax = parse_int_price(pair.get("tax"))
    if total is None or tax is None:
        return None
    return total - tax


def _select_paper(page, sel: dict, paper_no: int, after_paper_ms: int) -> bool:
    """wowpress 스티커는 제품마다 paperno5 가 있을 수도/없을 수도. 동적 처리.

    paperno5 element 존재 + 옵션 비어있지 않으면 → 3단 (paperno3 ancestor → paperno4 mid → paperno5 leaf)
    그 외 → 2단 (paperno3 ancestor → paperno4 leaf)
    """
    chain = page.evaluate(
        JS_PAPER_PARENT_CHAIN,
        {"list_id": sel.get("paper_list", "paperList"), "paperNo": paper_no},
    ) or []
    if not chain: return False

    p3_id = sel["paper_no3"]
    p3_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p3_id) or [])
    if not p3_opts: return False
    p3_idx = next((i for i, n in enumerate(chain) if str(n) in p3_opts), None)
    if p3_idx is None: return False
    if not js_set_select(page, p3_id, str(chain[p3_idx])): return False
    page.wait_for_timeout(after_paper_ms)

    # paperno5 가 존재 + 옵션 있는지 확인 (3단 vs 2단)
    p5_id = sel.get("paper_no5")
    p5_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p5_id) or []) if p5_id else set()

    p4_id = sel["paper_no4"]
    p4_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p4_id) or [])

    if p5_opts:
        # 3단 — paperno4 는 mid, paperno5 가 leaf
        p4_value = next((str(n) for n in chain[:p3_idx] if str(n) in p4_opts), None)
        if p4_value is None and p4_opts: p4_value = next(iter(p4_opts))
        if not p4_value: return False
        if not js_set_select(page, p4_id, p4_value): return False
        page.wait_for_timeout(after_paper_ms)
        p5_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p5_id) or [])
        if str(paper_no) not in p5_opts: return False
        if not js_set_select(page, p5_id, str(paper_no)): return False
    else:
        # 2단 — paperno4 가 leaf
        if str(paper_no) not in p4_opts: return False
        if not js_set_select(page, p4_id, str(paper_no)): return False
    page.wait_for_timeout(int(after_paper_ms * 0.7))
    return True


def _list_leaf_papers(page, sel: dict) -> list[dict]:
    """paperList JSON 의 leaf paper_no + 트리 경로 추출 (자식 없는 노드)."""
    plist = page.evaluate(f"""() => {{
        const el = document.getElementById('{sel.get("paper_list", "paperList")}');
        if (!el) return [];
        try {{ return JSON.parse(el.value); }} catch(e) {{ return []; }}
    }}""") or []
    pmap = {p["PaperNo"]: p for p in plist}
    children = {}
    for p in plist:
        if p["PaperNo"] == 0: continue
        children.setdefault(p.get("PaperPNo", 0), []).append(p["PaperNo"])
    leaves = [p for p in plist if not children.get(p["PaperNo"])]
    # full path
    def chain_text(no):
        out, cur = [], pmap.get(no)
        while cur and cur["PaperNo"]:
            out.append(cur.get("Name", ""))
            pno = cur.get("PaperPNo", 0)
            if not pno: break
            cur = pmap.get(pno)
        return out
    return [{"paper_no": lf["PaperNo"], "name": " ".join(reversed(chain_text(lf["PaperNo"])[1:-1])) + " " + chain_text(lf["PaperNo"])[0]}
            for lf in leaves]


def _set_size_req(page, size_select_id: str, size_value: str):
    page.evaluate(f"""() => {{
        const el = document.getElementById('{size_select_id}');
        if (!el) return;
        el.value = '{size_value}';
        if (typeof reqMdmDetail === 'function') {{
            reqMdmDetail('Size', '{size_value}', '0', 'hdata_00_sizeno');
        }}
        const oc = el.getAttribute('onchange');
        if (oc) {{ try {{ (new Function('event', oc)).call(el, null); }} catch(e) {{}} }}
    }}""")


class Adapter(SiteAdapter):
    site = "wowpress"
    category = "sticker"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("sticker", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})

        targets = [t for t in (ctx.targets or []) if isinstance(t, dict) and "prod_no" in t]
        if not targets:
            ctx.log.event("fetch.fail", level="warning", error="no targets")
            return

        with sync_playwright() as pw:
            browser, context = init_browser(pw, ctx)
            page = context.new_page()
            try:
                for t in targets:
                    ctx.log.event("fetch.start", product=t["product_name"], prod_no=t["prod_no"])
                    yield from self._crawl_product(ctx, page, t, sel, timeouts)
            finally:
                browser.close()

    def _crawl_product(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        try:
            page.goto(t["url"], wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error", product=t["product_name"], error="goto timeout")
            return
        page.wait_for_timeout(timeouts.get("after_goto_ms", 6000))

        # sizeno dropdown 옵션 dump → 표준 매칭
        size_opts = page.evaluate(JS_DUMP_OPTS, sel["size"]) or []
        size_matches = []
        for o in size_opts:
            if not o["value"]: continue
            wh = _parse_size(o["text"])
            if not wh: continue
            near = _near_canonical(*wh)
            if near:
                size_matches.append((f"{near[0]}x{near[1]}", o["value"], o["text"]))
        if not size_matches:
            ctx.log.event("extract.skip", product=t["product_name"],
                          reason=f"sizeno 옵션에 표준 매칭 0 (옵션={len(size_opts)})")
            return
        ctx.log.event("size.matched", product=t["product_name"], n=len(size_matches),
                      matches=[m[0] for m in size_matches])

        # paperList leaf 추출
        leaves = _list_leaf_papers(page, sel)
        if not leaves:
            ctx.log.event("extract.skip", product=t["product_name"], reason="paperList leaf 없음")
            return

        # 각 사이즈 × 각 paper leaf cascade
        for canonical, sz_value, sz_text in size_matches:
            ctx.log.event("size.start", product=t["product_name"], size=canonical)
            _set_size_req(page, sel["size"], sz_value)
            page.wait_for_timeout(timeouts.get("after_size_ms", 6000))

            # qty/ord_cnt 셋팅
            js_set_select(page, sel["qty"], DEFAULT_QTY)
            page.wait_for_timeout(timeouts.get("after_qty_ms", 500))
            if sel.get("ord_cnt"):
                js_set_select(page, sel["ord_cnt"], "1")
                page.wait_for_timeout(timeouts.get("after_qty_ms", 500))

            for lf in leaves:
                paper_no = lf["paper_no"]
                paper_name_cfg = lf["name"]
                if not _select_paper(page, sel, paper_no, timeouts.get("after_paper_ms", 2200)):
                    continue

                js_set_select(page, sel["qty"], DEFAULT_QTY)
                page.wait_for_timeout(timeouts.get("after_qty_ms", 500))
                if sel.get("ord_cnt"):
                    js_set_select(page, sel["ord_cnt"], "1")
                    page.wait_for_timeout(timeouts.get("after_qty_ms", 500))

                page.evaluate("() => { if (typeof fnOrdSummary === 'function') fnOrdSummary(); }")
                page.wait_for_timeout(timeouts.get("after_summary_ms", 2000))

                price = _read_supply_price(page, sel)
                if price is None or price <= 0:
                    page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                    price = _read_supply_price(page, sel)
                if price is None or price <= 0:
                    continue

                # DOM 실측
                p3 = js_get_select_text(page, sel["paper_no3"])
                p4 = js_get_select_text(page, sel["paper_no4"])
                p5 = js_get_select_text(page, sel["paper_no5"])
                paper_name = " ".join(x.strip() for x in (p3, p4, p5) if x and x.strip()) or paper_name_cfg

                yield RawItem(
                    product=t["product_name"],
                    category=t.get("category", "스티커"),
                    paper_name=paper_name,
                    coating=None,
                    print_mode=None,
                    size=canonical,
                    qty=int(DEFAULT_QTY),
                    price=price,
                    price_vat_included=False,
                    url=t["url"], url_ok=True,
                    options={
                        "paper_no": paper_no,
                        "sizeno_value": sz_value,
                        "sizeno_text": sz_text,
                        "config_paper": paper_name_cfg,
                        "shape": "사각형",
                    },
                )
