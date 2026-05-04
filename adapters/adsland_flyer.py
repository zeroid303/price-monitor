"""애즈랜드 합판전단 어댑터.

페이지: https://www.adsland.com/shop/order.php?IC=IC00023
DOM:
- size_book / paper / dosu / busu (paper×size 별 옵션 다름)
- busu 옵션 텍스트: "2,000장 (0.5연)" — 매수 직접 명시
- 가격: bill_ttl_sub (공급가)

수집 정책: paper×size 별 busu 옵션 dump → 표준 매수(2000)에 가장 가까운 매수 선택.
옵션 텍스트에서 매수 정수 추출.
"""
import re
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._adsland_card_common import (
    JS_GET_SELECT_OPTIONS, goto_with_wait, init_browser, js_set_select,
    js_get_select_text, price_with_retry, trigger_smart, JS_GET_SELECT_VALUE,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


TARGET_QTY_MAE = 2000


def _parse_busu_mae(text: str):
    """'2,000장 (0.5연)' / '1,000장 (1연)' → 2000 / 1000."""
    m = re.search(r"([0-9,]+)\s*장", text or "")
    if not m: return None
    return int(m.group(1).replace(",", ""))


class Adapter(SiteAdapter):
    site = "adsland"
    category = "flyer"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("flyer", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})
        guard = cat_cfg.get("low_price_guard", {})

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
                    yield from self._crawl(ctx, page, t, sel, timeouts, guard)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts, guard) -> Iterator[RawItem]:
        if not js_set_select(page, sel["kind"], "1"):
            ctx.log.event("extract.warn", product=t["product_name"], error="kind=1 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 500))

        for paper in t["papers"]:
            if not js_set_select(page, sel["paper"], paper["paper_value"]):
                ctx.log.event("extract.warn", product=t["product_name"],
                              paper=paper["paper_value"], error="paper 셋팅 실패")
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 500))

            for size in t["sizes"]:
                if not js_set_select(page, sel["size"], size["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))

                # busu 옵션 dump → 표준 매수에 가장 가까운 옵션 선택
                opts = page.evaluate(JS_GET_SELECT_OPTIONS, sel["busu"])
                opts = [o for o in (opts or []) if o.get("value")]
                if not opts:
                    continue
                # 옵션 텍스트에서 매수 추출
                cands = []
                for o in opts:
                    mae = _parse_busu_mae(o.get("text", ""))
                    if mae:
                        cands.append((mae, o["value"], o["text"]))
                if not cands:
                    continue
                # 표준 매수에 가장 가까운 것
                chosen_mae, chosen_value, chosen_text = min(cands, key=lambda c: abs(c[0] - TARGET_QTY_MAE))

                if not js_set_select(page, sel["busu"], chosen_value):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 500))

                for cm in t["color_modes"]:
                    if not js_set_select(page, sel["dosu"], cm["value"]):
                        continue
                    page.wait_for_timeout(timeouts.get("after_select_ms", 500))
                    trigger_smart(page)

                    price = price_with_retry(page, sel, chosen_mae, timeouts, guard)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper=paper["paper_value"], size=size["size_label"],
                                      color=cm["name"], error="price read failed")
                        continue

                    yield RawItem(
                        product=t["product_name"], category=t["category"],
                        paper_name=paper["paper_name_out"],
                        coating=None, print_mode=cm["name"],
                        size=size["size_label"],
                        qty=chosen_mae, price=price,
                        price_vat_included=False,
                        url=t["url"], url_ok=True,
                        options={"paper_value": paper["paper_value"],
                                 "size_value": size["value"],
                                 "dosu_value": cm["value"],
                                 "busu_value": chosen_value,
                                 "busu_text": chosen_text},
                    )
