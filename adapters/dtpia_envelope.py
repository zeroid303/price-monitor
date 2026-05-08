"""디티피아 봉투 어댑터.

페이지 2종:
  1. Standard.aspx (칼라봉투) — sdiv_cd / jijil_gb → mtrl_cd cascade / 단면4도 / 1000매
  2. Master.aspx (기성봉투 흑백) — en_category → en_type / sdiv_cd / 단면1도 / 1000매

가격: est_scroll_total_am (VAT 포함 → price_vat_included=True 로 raw 저장,
normalize 단계에서 ÷1.1 변환).
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


JS_SET_SELECT = """({sel_id, value}) => {
    const el = document.getElementById(sel_id);
    if (!el) return 'NO_EL';
    const wantedVal = String(value);
    const opt = [...el.options].find(o => o.value === wantedVal);
    if (!opt && wantedVal !== '') return 'NO_OPT';
    el.value = wantedVal;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""

JS_DUMP_SELECT = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return [];
    return [...el.options].map(o => ({value: o.value, text: o.textContent.trim()}));
}"""

JS_TRIGGER_PRICE = """() => {
    if (typeof callPrice === 'function') { try { callPrice(); } catch(e) {} }
}"""

JS_GET_PRICE = """() => {
    const el = document.getElementById('est_scroll_total_am');
    return el ? el.textContent.trim() : null;
}"""


def _parse_price(txt: Optional[str]) -> Optional[int]:
    if not txt: return None
    m = re.search(r"[\d,]+", txt.replace(" ", ""))
    if not m: return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


class Adapter(SiteAdapter):
    site = "dtpia"
    category = "envelope"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("envelope", {})
        sel = cat_cfg.get("selectors", {})
        timeouts = cat_cfg.get("timeouts", {})

        if not ctx.targets:
            ctx.log.event("fetch.fail", level="warning", error="no targets")
            return

        with sync_playwright() as pw:
            browser_cfg = ctx.site_config.get("browser", {})
            browser = pw.chromium.launch(headless=browser_cfg.get("headless", True))
            context = browser.new_context(
                viewport=browser_cfg.get("viewport", {"width": 1280, "height": 900}),
                locale=ctx.site_config.get("locale", "ko-KR"),
            )
            for pat in ctx.site_config.get("block_patterns", []):
                context.route(pat, lambda r: r.abort())
            context.on("dialog", lambda d: d.dismiss())
            page = context.new_page()
            try:
                for t in ctx.targets:
                    ctx.log.event("fetch.start", product=t["product_name"])
                    if t.get("page_type") == "dtpia_standard":
                        yield from self._crawl_standard(ctx, page, t, sel, timeouts)
                    elif t.get("page_type") == "dtpia_master":
                        yield from self._crawl_master(ctx, page, t, sel, timeouts)
            finally:
                browser.close()

    def _goto(self, page, url, timeouts, ctx, product) -> bool:
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
            page.wait_for_timeout(timeouts.get("after_goto_ms", 2500))
            return True
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error", product=product, error="goto timeout")
            return False

    def _crawl_standard(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        if not self._goto(page, t["url"], timeouts, ctx, t["product_name"]):
            return

        # 공통 옵션: 칼라4도, 1000매, 아래뚜껑 인쇄 N, 일반가공
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["color_mode"], "value": t.get("color_value", "4")})
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["qty"], "value": t.get("qty_value", "1000")})
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))
        if sel.get("cap_print"):
            page.evaluate(JS_SET_SELECT, {"sel_id": sel["cap_print"], "value": "N"})
            page.wait_for_timeout(300)
        if sel.get("cover_tomson"):
            page.evaluate(JS_SET_SELECT, {"sel_id": sel["cover_tomson"], "value": ""})
            page.wait_for_timeout(300)

        for sz in t["sizes"]:
            r = page.evaluate(JS_SET_SELECT, {"sel_id": sel["sdiv_cd"], "value": sz["sdiv_cd"]})
            if r is not True:
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))

            # jijil_gb 전수 — 모든 지질 dump
            jijils = page.evaluate(JS_DUMP_SELECT, sel["jijil_gb"])
            for jj in jijils:
                if not jj["value"]: continue
                if page.evaluate(JS_SET_SELECT, {"sel_id": sel["jijil_gb"], "value": jj["value"]}) is not True:
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 600))

                mtrls = page.evaluate(JS_DUMP_SELECT, sel["mtrl_cd"])
                for mm in mtrls:
                    if not mm["value"]: continue
                    if page.evaluate(JS_SET_SELECT, {"sel_id": sel["mtrl_cd"], "value": mm["value"]}) is not True:
                        continue
                    page.wait_for_timeout(timeouts.get("after_select_ms", 400))
                    page.evaluate(JS_TRIGGER_PRICE)
                    page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 1500))
                    price = _parse_price(page.evaluate(JS_GET_PRICE))
                    if not price or price <= 0:
                        continue

                    # paper raw = jijil_gb text + mtrl_cd text 결합 (예: "모조 RMO100")
                    paper_raw = f"{jj['text']} {mm['text']}".strip() if jj["text"] else mm["text"]
                    yield RawItem(
                        product=t["product_name"], category=t["category"],
                        paper_name=paper_raw,
                        coating=None, print_mode=t["print_mode"],
                        size=sz["size_label"], qty=int(t.get("qty_value", "1000")),
                        price=price, price_vat_included=True,
                        url=t["url"], url_ok=True,
                        options={"sdiv_cd": sz["sdiv_cd"],
                                 "jijil_gb": jj["value"], "jijil_text": jj["text"],
                                 "mtrl_cd": mm["value"], "mtrl_text": mm["text"]},
                    )

    def _crawl_master(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        if not self._goto(page, t["url"], timeouts, ctx, t["product_name"]):
            return

        page.evaluate(JS_SET_SELECT, {"sel_id": sel["color_mode"], "value": t.get("color_value", "1")})
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["qty"], "value": t.get("qty_value", "1000")})
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))

        # en_category 셋팅 (e.g., 'A' = 서류봉투)
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["en_category"], "value": t.get("en_category_value", "A")})
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))

        # en_type 전수
        types = page.evaluate(JS_DUMP_SELECT, sel["en_type"])
        for sz in t["sizes"]:
            page.evaluate(JS_SET_SELECT, {"sel_id": sel["sdiv_cd"], "value": sz["sdiv_cd"]})
            page.wait_for_timeout(timeouts.get("after_select_ms", 400))

            for ty in types:
                if not ty["value"]: continue
                if page.evaluate(JS_SET_SELECT, {"sel_id": sel["en_type"], "value": ty["value"]}) is not True:
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 400))
                page.evaluate(JS_TRIGGER_PRICE)
                page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 1500))
                price = _parse_price(page.evaluate(JS_GET_PRICE))
                if not price or price <= 0:
                    continue

                # en_type text 예: "4절 초특대형봉투 (크라프트 98g)" → 괄호 속 paper 추출
                m = re.search(r"\(([^)]+)\)\s*$", ty["text"])
                paper_raw = m.group(1).strip() if m else ty["text"]

                yield RawItem(
                    product=t["product_name"], category=t["category"],
                    paper_name=paper_raw,
                    coating=None, print_mode=t["print_mode"],
                    size=sz["size_label"], qty=int(t.get("qty_value", "1000")),
                    price=price, price_vat_included=True,
                    url=t["url"], url_ok=True,
                    options={"sdiv_cd": sz["sdiv_cd"],
                             "en_category": t.get("en_category_value", "A"),
                             "en_type": ty["value"], "en_type_text": ty["text"]},
                )
