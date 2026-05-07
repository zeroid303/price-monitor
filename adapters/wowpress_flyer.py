"""와우프레스 합판전단 어댑터.

페이지: https://wowpress.co.kr/ordr/prod/dets?ProdNo=40026
DOM:
- pdata_00_sizeno: 사이즈
- pdata_00_colorno: 단면(255) / 양면(256)
- spdata_00_paperno3: 아트지 / spdata_00_paperno4: 100g / 150g
- spdata_00_ordqty: 연 단위 — paper×size 별 가용 연수 다름
- 가격: od_00_totalcost - od_00_taxcost (공급가)

수집 정책: paper×size 별 가용 가장 작은 연 옵션 동적 선택.
1연당 매수는 wowpress 의 size 별 환산표 (사이트 정책) 기반:
  A2=1,000 / A3=2,000 / A4=4,000 / B3=2,000 / B4=4,000.
raw qty = yeon × per_yeon 매수.
"""

# wowpress 사이트 1연당 매수 정책표 (재단사이즈 기준)
SIZE_TO_PER_YEON = {
    "A2": 1000, "A3": 2000, "A4": 4000, "A5": 8000,
    "B3": 2000, "B4": 4000, "B5": 8000,
}
from typing import Iterator

from playwright.sync_api import sync_playwright

from adapters._wowpress_card_common import (
    JS_AVAIL_OPTIONS, goto_with_wait, init_browser,
    js_set_select, price_with_retry, select_paper,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


JS_GET_QTY_OPTS = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return [];
    return [...el.options].map(o => parseFloat(o.value)).filter(v => !isNaN(v));
}"""


class Adapter(SiteAdapter):
    site = "wowpress"
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
        for size in t["sizes"]:
            if not js_set_select(page, sel["size"], size["sizeno"]):
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 800))

            for cm in t["color_modes"]:
                if not js_set_select(page, sel["color_mode"], cm["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 800))

                for paper in t["papers"]:
                    if not select_paper(page, sel, paper["paper_no"]):
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper_no=paper["paper_no"], error="paper 셋팅 실패")
                        continue
                    page.wait_for_timeout(timeouts.get("after_paper_ms", 800))

                    # qty 가용 옵션 확인 후 가장 작은 연 선택
                    opts = page.evaluate(JS_GET_QTY_OPTS, sel["qty"])
                    if not opts:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper_no=paper["paper_no"], size=size["size_label"],
                                      color=cm["name"], error="qty 옵션 없음")
                        continue
                    chosen_yeon = min(opts)
                    js_set_select(page, sel["qty"], str(chosen_yeon))
                    page.wait_for_timeout(timeouts.get("after_qty_ms", 1200))

                    price = price_with_retry(page, sel, None, timeouts, guard)
                    if price is None:
                        ctx.log.event("extract.warn", product=t["product_name"],
                                      paper_no=paper["paper_no"], size=size["size_label"],
                                      color=cm["name"], error="price read failed")
                        continue

                    per_yeon = SIZE_TO_PER_YEON.get(size["size_label"])
                    qty_mae = int(chosen_yeon * per_yeon) if per_yeon else None

                    yield RawItem(
                        product=t["product_name"], category=t["category"],
                        paper_name=paper["paper_name_out"],
                        coating=None, print_mode=cm["name"],
                        size=size["size_label"],
                        qty=qty_mae, price=price,
                        price_vat_included=False,
                        url=t["url"], url_ok=True,
                        options={"paper_no": paper["paper_no"],
                                 "sizeno": size["sizeno"],
                                 "color_value": cm["value"],
                                 "qty_yeon": chosen_yeon,
                                 "per_yeon_mae": per_yeon},
                    )
