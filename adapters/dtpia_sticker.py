"""디티피아 사각형 스티커 어댑터.

페이지: Sticker/Jaedan.aspx (사각재단 스티커)
- sticker_type=jd (사각재단 고정)
- mtrl_cd 8옵션 (강접/초강접 아트지/유포지/투명데드롱/은데드롱 등)
- non_nrm_yn=N (규격) → size_gb select 14옵션 중 표준 8 사이즈 ±5mm 매칭
- coating_type=1 (유광코팅 기본)
- prn_sht_cn=1000

가격: callPrice() 트리거 → span#est_scroll_total_am 텍스트 (VAT 포함).
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


CANONICAL_SIZES = [
    (60, 40), (80, 50), (90, 55), (90, 60),
    (90, 70), (90, 80), (90, 100), (90, 120),
]
SIZE_TOL_MM = 5

DEFAULT_COATING = "1"     # 유광코팅
DEFAULT_QTY = "1000"


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
    return [...el.options].map(o => ({value: o.value, text: (o.textContent||'').trim()}));
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
    try: return int(m.group().replace(",", ""))
    except ValueError: return None


def _parse_size(text: str):
    """size_gb 옵션 text '60x40' → (60, 40). 매칭 안 되면 None."""
    m = re.search(r"(\d+)\s*[x×*X]\s*(\d+)", text)
    if not m: return None
    return (int(m.group(1)), int(m.group(2)))


def _near_canonical(w: int, h: int, tol: int = SIZE_TOL_MM):
    """표준 사이즈 매칭 — ±tol mm 내 첫 매칭. 일치 우선."""
    # exact match 우선
    for tw, th in CANONICAL_SIZES:
        if w == tw and h == th: return (tw, th)
    # tolerance
    for tw, th in CANONICAL_SIZES:
        if abs(w - tw) <= tol and abs(h - th) <= tol: return (tw, th)
    return None


class Adapter(SiteAdapter):
    site = "dtpia"
    category = "sticker"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        cat_cfg = ctx.site_config.get("sticker", {})
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
                    yield from self._crawl(ctx, page, t, sel, timeouts)
            finally:
                browser.close()

    def _crawl(self, ctx, page, t, sel, timeouts) -> Iterator[RawItem]:
        try:
            page.goto(t["url"], wait_until="domcontentloaded",
                      timeout=timeouts.get("page_goto_ms", 30000))
            page.wait_for_timeout(timeouts.get("after_goto_ms", 2500))
        except PwTimeout:
            ctx.log.event("fetch.fail", level="error", product=t["product_name"], error="goto timeout")
            return

        # 1. 공통 옵션 셋팅 (1회)
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["sticker_type"], "value": "jd"})
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["coating_type"], "value": DEFAULT_COATING})
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["non_nrm_yn"], "value": "N"})  # 규격
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))
        page.evaluate(JS_SET_SELECT, {"sel_id": sel["qty"], "value": DEFAULT_QTY})
        page.wait_for_timeout(timeouts.get("after_select_ms", 600))

        # 2. size_gb 옵션 dump → 표준 8 사이즈에 매칭되는 옵션 추출
        size_opts = page.evaluate(JS_DUMP_SELECT, sel["size_gb"])
        size_matches = []  # [(canonical_size_str, gb_value, gb_text)]
        for o in size_opts:
            if not o["value"]: continue
            wh = _parse_size(o["text"])
            if not wh: continue
            near = _near_canonical(*wh)
            if near:
                canonical = f"{near[0]}x{near[1]}"
                size_matches.append((canonical, o["value"], o["text"]))
        ctx.log.event("size.matched", n=len(size_matches),
                      matches=[m[0] for m in size_matches])

        # 3. mtrl_cd (paper) 8옵션 × 매칭 사이즈 cascade
        paper_opts = page.evaluate(JS_DUMP_SELECT, sel["mtrl_cd"])
        for paper in paper_opts:
            if not paper["value"]: continue
            if page.evaluate(JS_SET_SELECT, {"sel_id": sel["mtrl_cd"], "value": paper["value"]}) is not True:
                continue
            page.wait_for_timeout(timeouts.get("after_select_ms", 600))

            for canonical, gb_value, gb_text in size_matches:
                if page.evaluate(JS_SET_SELECT, {"sel_id": sel["size_gb"], "value": gb_value}) is not True:
                    continue
                page.wait_for_timeout(timeouts.get("after_select_ms", 600))

                # qty 재셋팅 (paper/size 변경 시 리셋 가능)
                page.evaluate(JS_SET_SELECT, {"sel_id": sel["qty"], "value": DEFAULT_QTY})
                page.wait_for_timeout(300)

                page.evaluate(JS_TRIGGER_PRICE)
                page.wait_for_timeout(timeouts.get("after_price_trigger_ms", 1500))
                price = _parse_price(page.evaluate(JS_GET_PRICE))
                if not price or price <= 0:
                    page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
                    price = _parse_price(page.evaluate(JS_GET_PRICE))
                if not price or price <= 0:
                    ctx.log.event("extract.skip", product=t["product_name"],
                                  paper=paper["text"], size=canonical, reason="가격 0")
                    continue

                yield RawItem(
                    product=t["product_name"],
                    category=t.get("category", "스티커"),
                    paper_name=paper["text"],
                    coating="유광코팅",
                    print_mode=None,  # dtpia 사각재단 페이지는 도수 select 없음 (default 칼라)
                    size=canonical,
                    qty=int(DEFAULT_QTY),
                    price=price,
                    price_vat_included=True,
                    url=t["url"], url_ok=True,
                    options={
                        "mtrl_cd": paper["value"],
                        "size_gb": gb_value,
                        "size_gb_text": gb_text,
                        "shape": "사각형",
                    },
                )
