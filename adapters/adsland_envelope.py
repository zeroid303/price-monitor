"""애즈랜드 봉투 어댑터.

페이지: IC00053 (칼라봉투, 단면4도)
  - size_book × paper_sort × tmp_paper 3단 cascade 전수조사.
  - paper_sort 변경 시 onchange=load_paper() → tmp_paper 갱신.
  - type_bt=일반형 / dosu=4/0 고정.

흑백/마스타지(IC00058) 는 수집 안 함 (정책 2026-05-11~).

가격: input[name="bill_ttl_sub"] (공급가 직접). smart() 트리거.

raw 원칙:
- paper_name = paper_sort + tmp_paper text 결합 (예: "모조지 백색 100g")
- size = size_book selected text
- print_mode = config 값 (dosu 페이지에 항상 노출되지 않음)
- price_vat_included=False
"""
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from adapters._adsland_card_common import (
    JS_GET_INPUT_VALUE,
    init_browser, js_set_select, js_get_select_text, js_get_select_options,
    parse_int_price, trigger_smart,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


def _read_supply_price(page, sel: dict) -> Optional[int]:
    try:
        v = page.evaluate(JS_GET_INPUT_VALUE, sel.get("price_supply"))
    except Exception:
        return None
    return parse_int_price(v)


def _price_with_retry(page, sel: dict, qty: int, timeouts: dict, guard: dict) -> Optional[int]:
    page.wait_for_timeout(timeouts.get("after_smart_ms", 700))
    price = _read_supply_price(page, sel)
    if price is None:
        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
        trigger_smart(page)
        page.wait_for_timeout(timeouts.get("after_smart_ms", 700))
        price = _read_supply_price(page, sel)
    if price is None:
        return None
    floor = max(guard.get("floor_abs", 500), qty * guard.get("per_qty_multiplier", 3))
    if price < floor:
        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
        trigger_smart(page)
        page.wait_for_timeout(timeouts.get("after_smart_ms", 700))
        price = _read_supply_price(page, sel)
        if price is None or price < floor:
            return None
    return price


class Adapter(SiteAdapter):
    site = "adsland"
    category = "envelope"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("envelope", {})
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
                    yield from self._crawl_color(ctx, page, t, sel, timeouts, guard)
            finally:
                browser.close()

    def _goto(self, page, url: str, timeouts: dict, ctx, product: str) -> bool:
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
            page.wait_for_timeout(timeouts.get("after_goto_ms", 3000))
            return True
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error",
                          product=product, error="goto timeout")
            return False

    def _crawl_color(self, ctx, page, t, sel, timeouts, guard) -> Iterator[RawItem]:
        if not self._goto(page, t["url"], timeouts, ctx, t["product_name"]):
            return

        # type_bt=일반형 고정
        type_bt_val = t.get("type_bt_value", "일반형")
        if not js_set_select(page, sel["type_bt"], type_bt_val):
            ctx.log.event("extract.warn", product=t["product_name"],
                          error=f"type_bt={type_bt_val} 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        # 매수 + 건수 셋팅
        qty_target = t.get("qty_target", "1000")
        if not js_set_select(page, sel["busu"], qty_target):
            ctx.log.event("extract.warn", product=t["product_name"], error="busu 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        js_set_select(page, sel["kind"], "1")
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        for sz in t["sizes"]:
            if not js_set_select(page, sel["size_book"], sz["size_value"]):
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))

            # paper_sort 9개 전체 순회
            for ps_opt in js_get_select_options(page, sel["paper_sort"]):
                if not ps_opt["value"]:
                    continue
                if not js_set_select(page, sel["paper_sort"], ps_opt["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 700))

                # tmp_paper 종속 옵션 dump → 각 tmp_paper 순회
                for tp_opt in js_get_select_options(page, sel["tmp_paper"]):
                    if not tp_opt["value"]:
                        continue
                    if not js_set_select(page, sel["tmp_paper"], tp_opt["value"]):
                        continue
                    # tmp_paper onchange=sel_busu();smart() — 가격 갱신
                    trigger_smart(page)
                    qty_int = int(qty_target)

                    price = _price_with_retry(page, sel, qty_int, timeouts, guard)
                    if price is None:
                        continue

                    paper_name = f"{ps_opt['text']} {tp_opt['text']}".strip()
                    size_text = js_get_select_text(page, sel["size_book"]) or sz["canonical"]
                    yield RawItem(
                        product=t["product_name"],
                        category=t.get("category", "봉투"),
                        paper_name=paper_name,
                        coating=None,
                        print_mode=t.get("print_mode", "단면4도"),
                        size=sz["canonical"],
                        qty=qty_int,
                        price=price,
                        price_vat_included=False,
                        url=t["url"], url_ok=True,
                        options={
                            "size_value": sz["size_value"],
                            "size_label": size_text,
                            "paper_sort": ps_opt["value"],
                            "tmp_paper":  tp_opt["value"],
                        },
                    )
