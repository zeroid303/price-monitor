"""애즈랜드(adsland.com) 명함 어댑터 공통 헬퍼.

페이지 메커니즘:
- 모든 select 변경 후 가격 갱신은 `smart()` 함수 호출 (PHP 페이지: inline onchange,
  Vue 페이지: Vue method 내부에서 smart 호출). 어댑터는 set 후 명시적으로
  `window.smart()` 한 번 더 호출해서 동기화 보강.
- 가격 = `input[name="bill_ttl_sub"]` (공급가). 부가세 cross-check 가능.

raw 원칙:
- paper_name = paper select selected text (offset) 또는
              paperSort + paper + pweight 결합 (digital, DOM 표시값 그대로)
- coating   = coat select selected text (digital). offset 페이지는 None.
- print_mode = dosu/dosu_cover_out/dosu_cover_in selected text (offset)
              또는 익명 도수 select 2개 (앞면/뒷면) 결합 (digital, DOM 표시값 그대로)
- size       = size_book select selected text
- qty        = busu × kind. kind=1 고정이므로 qty = busu value (정수).
              digital 은 busuSelect × kind=1 고정이므로 busuSelect value.
- price      = bill_ttl_sub input value (정수 변환)
"""
import re
from typing import Optional

from playwright.sync_api import TimeoutError as PwTimeout

from engine.context import RawItem, RunContext


# ── JS 헬퍼 ──

JS_SET_SELECT = """({selector, value}) => {
    const el = document.querySelector(selector);
    if (!el) return 'NO_EL';
    const wantedVal = String(value);
    const opt = [...el.options].find(o => o.value === wantedVal);
    if (!opt) return 'NO_OPT';
    el.value = wantedVal;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    if (window.jQuery) { try { window.jQuery(el).trigger('change'); } catch(e) {} }
    return true;
}"""

JS_GET_SELECT_TEXT = """(selector) => {
    const el = document.querySelector(selector);
    if (!el || el.selectedIndex < 0) return '';
    return (el.options[el.selectedIndex]?.textContent || '').trim();
}"""

JS_GET_SELECT_VALUE = """(selector) => {
    const el = document.querySelector(selector);
    return el ? (el.value || '') : '';
}"""

JS_GET_SELECT_OPTIONS = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return [];
    return [...el.options].map(o => ({value: o.value, text: o.textContent.trim()}));
}"""

JS_GET_INPUT_VALUE = """(selector) => {
    const el = document.querySelector(selector);
    return el ? (el.value || '') : '';
}"""

# 가격 갱신 트리거. PHP 페이지는 inline onchange 가 이미 smart() 호출하지만,
# Vue 페이지는 Vue method 비동기로 인해 가끔 누락. 명시 호출로 동기화 보강.
JS_TRIGGER_SMART = """() => {
    if (typeof window.smart === 'function') {
        try { window.smart(); return true; } catch(e) { return 'ERR:' + e.message; }
    }
    return 'NO_SMART';
}"""

# 디지털 페이지 익명 도수 select 2개 식별 (앞면 / 뒷면).
# 앞면 = '앞면' 텍스트 포함 옵션이 있는 select.
# 뒷면 = '뒷면' 텍스트 포함 옵션이 있는 select.
JS_FIND_DIGITAL_DOSU = """() => {
    const sels = [...document.querySelectorAll('select')];
    function findContaining(keyword) {
        return sels.find(s =>
            [...s.options].some(o => (o.textContent || '').includes(keyword))
        );
    }
    const front = findContaining('앞면');
    const back  = findContaining('뒷면');
    function info(s) {
        if (!s) return null;
        return {
            id: s.id || null,
            name: s.name || null,
            options: [...s.options].map(o => ({value: o.value, text: (o.textContent||'').trim()}))
        };
    }
    return {front: info(front), back: info(back)};
}"""

# 디지털 페이지 도수 select 의 selected text 읽기 (앞/뒷면).
JS_GET_DIGITAL_DOSU_TEXT = """() => {
    const sels = [...document.querySelectorAll('select')];
    function pick(keyword) {
        const s = sels.find(x =>
            [...x.options].some(o => (o.textContent || '').includes(keyword))
        );
        if (!s || s.selectedIndex < 0) return '';
        return (s.options[s.selectedIndex]?.textContent || '').trim();
    }
    return {front: pick('앞면'), back: pick('뒷면')};
}"""

# 디지털 페이지 뒷면 select 셋팅 (옵션 value 기반).
JS_SET_DIGITAL_BACK_DOSU = """(value) => {
    const sels = [...document.querySelectorAll('select')];
    const s = sels.find(x =>
        [...x.options].some(o => (o.textContent || '').includes('뒷면'))
    );
    if (!s) return 'NO_EL';
    const opt = [...s.options].find(o => o.value === String(value));
    if (!opt) return 'NO_OPT';
    s.value = String(value);
    s.dispatchEvent(new Event('change', {bubbles: true}));
    if (window.jQuery) { try { window.jQuery(s).trigger('change'); } catch(e) {} }
    return true;
}"""


# ── 가격 파싱 ──

def parse_int_price(txt: Optional[str]) -> Optional[int]:
    if not txt:
        return None
    m = re.search(r"[\d,]+", txt)
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


# ── 셋 유틸 ──

def js_set_select(page, selector: str, value) -> bool:
    try:
        return page.evaluate(JS_SET_SELECT, {"selector": selector, "value": value}) is True
    except Exception:
        return False


def js_get_select_text(page, selector: str) -> str:
    try:
        return page.evaluate(JS_GET_SELECT_TEXT, selector) or ""
    except Exception:
        return ""


def js_get_select_options(page, selector: str) -> list[dict]:
    try:
        return page.evaluate(JS_GET_SELECT_OPTIONS, selector) or []
    except Exception:
        return []


def trigger_smart(page) -> None:
    try:
        page.evaluate(JS_TRIGGER_SMART)
    except Exception:
        pass


def read_supply_price(page, sel: dict) -> Optional[int]:
    try:
        v = page.evaluate(JS_GET_INPUT_VALUE, sel.get("price_supply"))
    except Exception:
        return None
    return parse_int_price(v)


# ── 브라우저 셋업 ──

def init_browser(pw, ctx: RunContext):
    browser_cfg = ctx.site_config.get("browser", {})
    browser = pw.chromium.launch(headless=browser_cfg.get("headless", True))
    vp = browser_cfg.get("viewport", {"width": 1280, "height": 900})
    context = browser.new_context(
        viewport={"width": vp["width"], "height": vp["height"]},
        locale=ctx.site_config.get("locale", "ko-KR"),
    )
    for pat in ctx.site_config.get("block_patterns", []):
        context.route(pat, lambda r: r.abort())
    context.on("dialog", lambda d: d.dismiss())
    return browser, context


def goto_with_wait(
    page, url: str, timeouts: dict, ctx: RunContext, product: str,
    ready_select: str = "select#size_book",
) -> bool:
    """페이지 진입 + size_book select 의 옵션이 채워질 때까지 대기.

    연속 navigation 시 domcontentloaded 직후엔 Vue/JS 가 옵션을 아직 안 채운
    상태일 수 있어, ready_select 의 options.length>1 까지 wait_for_function 추가.
    """
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=timeouts.get("page_goto_ms", 30000))
        page.wait_for_timeout(timeouts.get("after_goto_ms", 2500))
        if ready_select:
            try:
                page.wait_for_function(
                    f"() => {{ const el = document.querySelector({ready_select!r}); "
                    f"return el && el.options && el.options.length > 1; }}",
                    timeout=15000,
                )
            except PwTimeout:
                ctx.log.event("fetch.fail", level="warning",
                              product=product,
                              error=f"ready_select 옵션 채움 timeout: {ready_select}")
                return False
        return True
    except PwTimeout:
        ctx.log.event("fetch.fail", level="error",
                      product=product, error="page goto timeout")
        return False


def price_with_retry(page, sel: dict, qty: int, timeouts: dict, guard: dict) -> Optional[int]:
    page.wait_for_timeout(timeouts.get("after_smart_ms", 600))
    price = read_supply_price(page, sel)
    if price is None:
        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
        trigger_smart(page)
        page.wait_for_timeout(timeouts.get("after_smart_ms", 600))
        price = read_supply_price(page, sel)
        if price is None:
            return None
    floor = max(guard.get("floor_abs", 500), qty * guard.get("per_qty_multiplier", 3))
    if price < floor:
        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
        trigger_smart(page)
        page.wait_for_timeout(timeouts.get("after_smart_ms", 600))
        price = read_supply_price(page, sel)
        if price is None or price < floor:
            return None
    return price
