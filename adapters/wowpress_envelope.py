"""와우프레스 봉투 어댑터.

페이지 2종:
  1. 칼라봉투 (ProdNo=40034, ColorNo=255 칼라4도)
  2. 마스타봉투 단면흑백 (ProdNo=40035, ColorNo='' 기본 먹1도)

페이지 메커니즘 (카드와 동일하지만 paperno5 까지 사용):
- 사이즈 변경: reqMdmDetail('Size', size_value, '0', 'hdata_00_sizeno') →
  AJAX paperno3 옵션 갱신. 5~6초 대기 필수.
- 용지: paperno3 → paperno4 → paperno5 cascade.
  paperList JSON 의 leaf paper_no 부모 체인에서 ancestor 매칭.
- 가격: od_00_totalcost (VAT 포함) — 공급가 = totalcost - taxcost.

raw 원칙:
- paper_name = paperno3 + paperno4 + paperno5 selected text 결합
- size = pdata_00_sizeno text
- print_mode = pdata_00_colorno text (빈 ColorNo 면 기본 표시)
- price_vat_included=False (공급가 추출하므로)
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


def _select_paper_5(page, sel: dict, paper_no: int, after_paper_ms: int) -> bool:
    """봉투용 paper select — paperno3 → paperno4 → paperno5 까지 셋팅.

    chain[0] = leaf, chain[-1] = root. paperno5 = leaf paper_no.
    """
    chain = page.evaluate(
        JS_PAPER_PARENT_CHAIN,
        {"list_id": sel.get("paper_list", "paperList"), "paperNo": paper_no},
    ) or []
    if not chain:
        return False

    p3_id = sel["paper_no3"]
    p3_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p3_id) or [])
    if not p3_opts:
        return False
    p3_idx = next((i for i, n in enumerate(chain) if str(n) in p3_opts), None)
    if p3_idx is None:
        return False
    if not js_set_select(page, p3_id, str(chain[p3_idx])):
        return False
    page.wait_for_timeout(after_paper_ms)

    p4_id = sel["paper_no4"]
    p4_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p4_id) or [])
    p4_value = next((str(n) for n in chain[:p3_idx] if str(n) in p4_opts), None)
    if p4_value is None and p4_opts:
        p4_value = next(iter(p4_opts))
    if not p4_value:
        return False
    if not js_set_select(page, p4_id, p4_value):
        return False
    page.wait_for_timeout(after_paper_ms)

    p5_id = sel["paper_no5"]
    p5_opts = set(page.evaluate(JS_AVAIL_OPTIONS, p5_id) or [])
    if str(paper_no) in p5_opts:
        p5_value = str(paper_no)
    elif p5_opts:
        # leaf 가 dropdown 에 없으면 chain 다른 후보 또는 첫 옵션
        p5_value = next((str(n) for n in chain if str(n) in p5_opts), next(iter(p5_opts)))
    else:
        return False
    if not js_set_select(page, p5_id, p5_value):
        return False
    page.wait_for_timeout(int(after_paper_ms * 0.7))
    return True


def _set_size_req(page, size_select_id: str, size_value: str):
    """사이즈 변경 — reqMdmDetail AJAX 트리거 + onchange 호출."""
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


def _read_dom_state(page, sel: dict) -> dict:
    p3 = js_get_select_text(page, sel["paper_no3"])
    p4 = js_get_select_text(page, sel["paper_no4"])
    p5 = js_get_select_text(page, sel["paper_no5"])
    paper_name = " ".join(x.strip() for x in (p3, p4, p5) if x and x.strip()) or None
    size = js_get_select_text(page, sel["size"]) or None
    color = js_get_select_text(page, sel["color_mode"]) or None
    qty_val = page.evaluate(JS_GET_SELECT_VALUE, sel["qty"])
    try:
        qty = int(qty_val) if qty_val else None
    except (TypeError, ValueError):
        qty = None
    return {
        "paper_name": paper_name,
        "size": size,
        "print_mode": color,
        "qty": qty,
    }


class Adapter(SiteAdapter):
    site = "wowpress"
    category = "envelope"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("envelope", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})

        # dict-only target wrapping (옛 _notes 같은 항목 제외)
        targets = [t for t in (ctx.targets or []) if isinstance(t, dict) and "prod_no" in t]
        if not targets:
            ctx.log.event("fetch.fail", level="warning", error="no targets")
            return

        with sync_playwright() as pw:
            browser, context = init_browser(pw, ctx)
            page = context.new_page()
            try:
                for t in targets:
                    ctx.log.event("fetch.start", product=t["product_name"])
                    yield from self._crawl_product(ctx, page, t, sel, timeouts)
            finally:
                browser.close()

    def _crawl_product(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        try:
            page.goto(t["url"], wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error",
                          product=t["product_name"], error="goto timeout")
            return
        page.wait_for_timeout(timeouts.get("after_goto_ms", 6000))

        for sz in t["sizes"]:
            ctx.log.event("size.start",
                          product=t["product_name"],
                          size=sz["canonical"], size_value=sz["size_value"])
            _set_size_req(page, sel["size"], sz["size_value"])
            page.wait_for_timeout(timeouts.get("after_size_ms", 6000))

            # color + qty + ord_cnt 적용 (사이즈 변경 후마다 재셋팅)
            color_no = t.get("color_no", "")
            if color_no:
                js_set_select(page, sel["color_mode"], color_no)
                page.wait_for_timeout(timeouts.get("after_select_ms", 800))
            js_set_select(page, sel["qty"], t.get("qty_target", "1000"))
            page.wait_for_timeout(timeouts.get("after_qty_ms", 500))
            if sel.get("ord_cnt"):
                js_set_select(page, sel["ord_cnt"], "1")
                page.wait_for_timeout(timeouts.get("after_qty_ms", 500))

            for paper in t["papers"]:
                paper_no = paper["paper_no"]
                paper_name_cfg = paper["name"]
                if not _select_paper_5(page, sel, paper_no,
                                       timeouts.get("after_paper_ms", 2200)):
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  size=sz["canonical"], paper=paper_name_cfg,
                                  error="paper select 실패")
                    continue

                # 용지 변경 후 qty/ord_cnt 재적용 (ColorNo 는 건드리면 paper 리셋되므로 skip)
                js_set_select(page, sel["qty"], t.get("qty_target", "1000"))
                page.wait_for_timeout(timeouts.get("after_qty_ms", 500))
                if sel.get("ord_cnt"):
                    js_set_select(page, sel["ord_cnt"], "1")
                    page.wait_for_timeout(timeouts.get("after_qty_ms", 500))

                # 가격 계산 트리거
                page.evaluate("() => { if (typeof fnOrdSummary === 'function') fnOrdSummary(); }")
                page.wait_for_timeout(timeouts.get("after_summary_ms", 2000))

                price = _read_supply_price(page, sel)
                if price is None or price <= 0:
                    page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                    price = _read_supply_price(page, sel)
                if price is None or price <= 0:
                    ctx.log.event("extract.skip", product=t["product_name"],
                                  size=sz["canonical"], paper=paper_name_cfg,
                                  reason="1000매 가격 0 (미공급)")
                    continue

                dom = _read_dom_state(page, sel)
                yield RawItem(
                    product=t["product_name"],
                    category=t.get("category", "봉투"),
                    paper_name=dom["paper_name"],
                    coating=None,
                    print_mode=dom["print_mode"] or t.get("print_mode"),
                    size=sz["canonical"],
                    qty=dom["qty"] or int(t.get("qty_target", "1000")),
                    price=price,
                    price_vat_included=False,
                    url=t["url"], url_ok=True,
                    options={
                        "paper_no":         paper_no,
                        "size_value":       sz["size_value"],
                        "size_label":       sz.get("label"),
                        "config_paper":     paper_name_cfg,
                        "config_print_mode": t.get("print_mode"),
                    },
                )
