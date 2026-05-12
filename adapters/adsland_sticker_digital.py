"""애즈랜드 디지털 스티커 어댑터.

페이지: IC00119 (완전컷팅 스티커, 디지털)
- size_book 14옵션 → 표준 8 ±5mm 매칭
- paperSort[] 2 (일반 스티커 / 그문드스티커) → paper[] 5 종속 (paperSort 변경 시 paper 갱신)
- printBg 1 (배경있음), busuSelect 35배수 (35/70/105/140/...) — 100매 표준에 105매
- kind 1, csize1/csize2 input (사용 안 함)
- dosu 없음 (offset 페이지와 차이)

가격: bill_ttl_sub (공급가 직접) — smart() 트리거.
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from adapters._adsland_card_common import (
    JS_GET_INPUT_VALUE,
    init_browser, js_set_select, js_get_select_text, js_get_select_options,
    parse_int_price, trigger_smart,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


CANONICAL_SIZES = [
    (60, 40), (80, 50), (90, 55), (90, 60),
    (90, 70), (90, 80), (90, 100), (90, 120),
]
SIZE_TOL_MM = 5
DEFAULT_QTY = "105"


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
    try:
        v = page.evaluate(JS_GET_INPUT_VALUE, sel.get("price_supply"))
    except Exception:
        return None
    return parse_int_price(v)


def _price_with_retry(page, sel, qty, timeouts, guard):
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
    category = "sticker_digital"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("sticker_digital", {})
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
                    yield from self._crawl(ctx, page, t, sel, timeouts, guard)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts, guard) -> Iterator[RawItem]:
        try:
            page.goto(t["url"], wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
            page.wait_for_timeout(timeouts.get("after_goto_ms", 3000))
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error",
                          product=t["product_name"], error="goto timeout")
            return

        # 공통: busu=105, kind=1
        js_set_select(page, sel["busu_select"], DEFAULT_QTY)
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))
        js_set_select(page, sel["kind"], "1")
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        # size_book 표준 매핑
        size_opts = js_get_select_options(page, sel["size_book"])
        size_matches = []
        for o in size_opts:
            if not o["value"]: continue
            wh = _parse_size(o["text"])
            if not wh: continue
            near = _near_canonical(*wh)
            if near:
                size_matches.append((f"{near[0]}x{near[1]}", o["value"], o["text"]))
        ctx.log.event("size.matched", n=len(size_matches),
                      matches=[m[0] for m in size_matches])

        # paperSort 2 × paper N (paperSort 변경 시 paper 갱신)
        paper_sort_opts = js_get_select_options(page, sel["paper_sort"])
        for ps in paper_sort_opts:
            if not ps["value"]: continue
            if not js_set_select(page, sel["paper_sort"], ps["value"]):
                continue
            page.wait_for_timeout(timeouts.get("after_paper_chain_ms", 800))

            paper_opts = js_get_select_options(page, sel["paper"])
            for paper in paper_opts:
                if not paper["value"]: continue
                if not js_set_select(page, sel["paper"], paper["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 700))

                for canonical, sb_value, sb_text in size_matches:
                    if not js_set_select(page, sel["size_book"], sb_value):
                        continue
                    page.wait_for_timeout(timeouts.get("after_select_ms", 700))

                    js_set_select(page, sel["busu_select"], DEFAULT_QTY)
                    page.wait_for_timeout(300)
                    js_set_select(page, sel["kind"], "1")
                    page.wait_for_timeout(300)
                    trigger_smart(page)

                    qty_int = int(DEFAULT_QTY)
                    price = _price_with_retry(page, sel, qty_int, timeouts, guard)
                    if price is None:
                        continue

                    yield RawItem(
                        product=t["product_name"],
                        category=t.get("category", "스티커"),
                        paper_name=paper["text"],
                        coating=None,
                        print_mode="단면칼라",
                        size=canonical,
                        qty=qty_int,
                        price=price,
                        price_vat_included=False,
                        url=t["url"], url_ok=True,
                        options={
                            "paper_sort": ps["value"],
                            "paper_value": paper["value"],
                            "size_book": sb_value,
                            "size_book_text": sb_text,
                            "shape": "사각형",
                        },
                    )
