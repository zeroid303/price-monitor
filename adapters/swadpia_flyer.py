"""성원애드피아 합판전단 어댑터.

페이지: https://www.swadpia.co.kr/goods/goods_view/CLF1000/GLF1001
DOM:
- paper_type → paper_code 종속
- paper_size: A2/A3/A4/B3/B4/B5
- fside_color_amount=4 (고정) + bside_color_amount=0(단면)/4(양면)
- paper_qty: paper×size 별 가용 매수 옵션 다름. 옵션값이 매수 직접 (e.g., '2000')
- 가격: tr.estimate_supply_amt td.price (공급가)

수집 정책 (2026-05-21~):
  target 의 qty_targets_by_size[size] 매수 리스트마다 paper_qty dropdown 에서
  가장 가까운 옵션 선택. A4 만 2회, 나머지 사이즈는 1회. raw qty = 선택된 옵션 매수.
"""
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._swadpia_card_common import (
    JS_GET_SELECT_VALUE, init_browser, goto_with_wait,
    js_set_select, js_get_select_text, read_supply_price, parse_price,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


JS_GET_QTY_OPTS = "(sel) => { const el=document.querySelector(sel); if(!el) return []; return [...el.options].map(o=>parseInt(o.value, 10)).filter(v=>!isNaN(v) && v>0); }"


class Adapter(SiteAdapter):
    site = "swadpia"
    category = "flyer"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("flyer", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning", error="no targets")
            return

        with sync_playwright() as pw:
            browser, context = init_browser(pw, ctx)
            page = context.new_page()
            try:
                for t in ctx.targets:
                    ctx.log.event("fetch.start", product=t["product_name"])
                    if not goto_with_wait(page, t["url"], timeouts, ctx, t["product_name"]):
                        continue
                    yield from self._crawl(ctx, page, t, sel, timeouts)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        # fside_color_amount 항상 4 (앞면 칼라 고정)
        js_set_select(page, sel["fside_color_amount"], "4")
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))

        for combo in t["paper_combos"]:
            # paper_type → paper_code 종속
            if not js_set_select(page, sel["paper_type"], combo["paper_type"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              paper=combo["paper_name_out"], error="paper_type 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))
            if not js_set_select(page, sel["paper_code"], combo["paper_code"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              paper=combo["paper_code"], error="paper_code 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))

            qty_targets_by_size = t.get("qty_targets_by_size", {})
            for size in t["sizes"]:
                if not js_set_select(page, sel["paper_size"], size["paper_size"]):
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  paper=combo["paper_name_out"], size=size["size_label"],
                                  error=f"paper_size {size['paper_size']} 셋팅 실패 — 사이트 미제공 가능")
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 600))

                # paper×size 별 qty 옵션 dump
                qty_opts = page.evaluate(JS_GET_QTY_OPTS, sel["paper_qty"])
                if not qty_opts:
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  paper=combo["paper_name_out"], size=size["size_label"],
                                  error="qty 옵션 없음")
                    continue

                targets_mae = qty_targets_by_size.get(size["size_label"], [])
                if not targets_mae:
                    ctx.log.event("extract.warn", product=t["product_name"],
                                  size=size["size_label"], error="qty_targets_by_size 미정의")
                    continue

                for target_mae in targets_mae:
                    chosen_qty = min(qty_opts, key=lambda q: abs(q - target_mae))
                    if not js_set_select(page, sel["paper_qty"], str(chosen_qty)):
                        continue
                    page.wait_for_timeout(timeouts.get("after_select_ms", 600))

                    for cm in t["color_modes"]:
                        js_set_select(page, sel["fside_color_amount"], cm["fside_color_amount"])
                        page.wait_for_timeout(timeouts.get("after_select_ms", 600))
                        js_set_select(page, sel["bside_color_amount"], cm["bside_color_amount"])
                        page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 700))

                        price = read_supply_price(page, sel["price_supply"], 0)
                        if price is None:
                            page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                            price = read_supply_price(page, sel["price_supply"], 0)
                        if price is None:
                            ctx.log.event("extract.warn", product=t["product_name"],
                                          paper=combo["paper_name_out"], size=size["size_label"],
                                          target_mae=target_mae,
                                          color=cm["name"], error="price read failed")
                            continue

                        yield RawItem(
                            product=t["product_name"], category=t["category"],
                            paper_name=combo["paper_name_out"],
                            coating=None, print_mode=cm["name"],
                            size=size["size_label"],
                            qty=chosen_qty, price=price,
                            price_vat_included=False,
                            url=t["url"], url_ok=True,
                            options={"paper_type": combo["paper_type"],
                                     "paper_code": combo["paper_code"],
                                     "paper_size": size["paper_size"],
                                     "target_mae": target_mae,
                                     "qty_options_first": qty_opts[:5]},
                        )
