"""성원애드피아 봉투 어댑터.

페이지: CEV1000/GEV1001 (대중소봉투, 단면4도 칼라)
  - cal_line=BDC40 (단면칼라4도) 고정
  - paper_kind → paper_type → paper_code 3단 cascade 전수조사
  - bongto_kind=CE1 (규격형), bongto_type 으로 사이즈

흑백/기성형(CEV5000) 은 수집 안 함 (정책 2026-05-11~).

가격: #print_estimate_tot ('총 합계금액 ₩X,XXX 원' VAT 포함)
→ price_vat_included=True 로 raw 저장, normalize 단계에서 ÷1.1.

봉투 페이지는 coating UI 없음 → coating=None.
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright

from adapters._swadpia_card_common import (
    init_browser, goto_with_wait, js_set_select,
)
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


JS_DUMP_SELECT = """(selector) => {
    const s = document.querySelector(selector);
    if (!s) return [];
    return [...s.options].map(o => ({value: o.value, text: o.textContent.trim()}));
}"""

JS_GET_PRICE = """(selector) => {
    const e = document.querySelector(selector);
    if (!e) return null;
    return e.textContent.replace(/\\s+/g, ' ').trim();
}"""

_RE_TOTAL = re.compile(r"총\s*합계금액\s*[:：]?\s*[\\￦₩]?\s*([\d,]+)\s*원")


def _parse_total_price(txt: Optional[str]) -> Optional[int]:
    if not txt:
        return None
    m = _RE_TOTAL.search(txt)
    if not m:
        m2 = re.search(r"([\d,]{4,})", txt)
        if not m2:
            return None
        try:
            return int(m2.group(1).replace(",", ""))
        except ValueError:
            return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _dump(page, selector: str) -> list[dict]:
    try:
        return page.evaluate(JS_DUMP_SELECT, selector) or []
    except Exception:
        return []


class Adapter(SiteAdapter):
    site = "swadpia"
    category = "envelope"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("envelope", {})
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
                    yield from self._crawl_product(ctx, page, t, sel, timeouts)
            finally:
                browser.close()

    def _crawl_product(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        # 1. cal_line=BDC40 (단면칼라4도) + 매수 셋팅 (사이즈/용지 와 무관)
        if not js_set_select(page, sel["cal_line"], t.get("print_mode_value", "BDC40")):
            ctx.log.event("extract.warn", product=t["product_name"], error="cal_line 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        if not js_set_select(page, sel["paper_qty"], t.get("qty_value", "1000")):
            ctx.log.event("extract.warn", product=t["product_name"], error="qty 셋팅 실패")
            return
        page.wait_for_timeout(timeouts.get("after_select_ms", 700))

        for sz in t["sizes"]:
            # 2. 사이즈 셋팅 (bongto_kind → bongto_type)
            if not js_set_select(page, sel["bongto_kind"], sz["bongto_kind"]):
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))
            if not js_set_select(page, sel["bongto_type"], sz["bongto_type"]):
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 700))

            # 3. paper_kind → paper_type → paper_code 3단 전수
            for pk in _dump(page, sel["paper_kind"]):
                if not pk["value"]:
                    continue
                if not js_set_select(page, sel["paper_kind"], pk["value"]):
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 700))

                for pt in _dump(page, sel["paper_type"]):
                    if not pt["value"]:
                        continue
                    if not js_set_select(page, sel["paper_type"], pt["value"]):
                        continue
                    page.wait_for_timeout(timeouts.get("after_select_ms", 700))

                    yield from self._iter_paper_codes(
                        ctx, page, t, sel, timeouts, sz,
                        pk_text=pk["text"], pt_text=pt["text"],
                    )

    def _iter_paper_codes(self, ctx, page, t, sel, timeouts, sz,
                          pk_text: str, pt_text: str) -> Iterator[RawItem]:
        for pc in _dump(page, sel["paper_code"]):
            if not pc["value"]:
                continue
            if not js_set_select(page, sel["paper_code"], pc["value"]):
                continue
            page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 800))

            txt = page.evaluate(JS_GET_PRICE, sel["price_total"])
            price = _parse_total_price(txt or "")
            if not price or price <= 0:
                page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                txt = page.evaluate(JS_GET_PRICE, sel["price_total"])
                price = _parse_total_price(txt or "")
            if not price or price <= 0:
                continue

            paper_name_raw = f"{pt_text} {pc['text']}".strip() if pt_text else pc["text"]

            yield RawItem(
                product=t["product_name"],
                category=t.get("category", "봉투"),
                paper_name=paper_name_raw or None,
                coating=None,
                print_mode=t.get("print_mode", "단면4도"),
                size=sz["canonical"],
                qty=int(t.get("qty_value", "1000")),
                price=price,
                price_vat_included=True,
                url=t["url"], url_ok=True,
                options={
                    "bongto_kind": sz["bongto_kind"],
                    "bongto_type": sz["bongto_type"],
                    "size_label": sz.get("label"),
                    "paper_kind_text": pk_text,
                    "paper_type_text": pt_text,
                    "paper_code_value": pc["value"],
                    "paper_code_text": pc["text"],
                },
            )
