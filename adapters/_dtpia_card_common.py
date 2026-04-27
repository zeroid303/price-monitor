"""디티피아 명함 어댑터 공통 헬퍼.

페이지 type 5가지의 공통 로직 + 분기 함수.
- A: page_fixed (일반명함)              → 페이지 고정 paper, coating select
- B: mtrl_cd_pair (고급지/펄지/...)     → mtrl_cd + mtrl_cdw select
- C: mtrl_cd_only (PP카드)              → mtrl_cd select만, size hidden
- D: mtrl_split (피아노블랙박/소량/인디고) → 두 select (id 만 다름)

raw 원칙:
- paper_name = .mtrl-name div text (모든 페이지 일관)
- paper_weight_text = weight select text 그대로 (예: "230g")
- coating = coating_type select text
- print_mode = prn_clr_cn_gb select text
- size = ppr_cut_tmp select text 또는 PP카드의 페이지 표시 텍스트
- 합성/조작 금지. 못 읽으면 None.
"""
import re
from typing import Iterator, Optional

from playwright.sync_api import TimeoutError as PwTimeout

from engine.context import RawItem, RunContext


# ── JS 상수 ──

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

JS_AVAIL_OPTIONS = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el || el.tagName !== 'SELECT') return [];
    return [...el.options].map(o => String(o.value));
}"""

JS_TRIGGER_PRICE = """() => {
    if (typeof callPrice === 'function') {
        try { callPrice(); } catch(e) {}
    }
}"""

JS_GET_PRICE = """(sel_id) => {
    const el = document.getElementById(sel_id);
    return el ? el.textContent.trim() : null;
}"""

# selected text 추출 (SELECT 가 아니면 빈 문자열)
JS_GET_SELECT_TEXT = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el || el.tagName !== 'SELECT' || el.selectedIndex < 0) return '';
    return (el.options[el.selectedIndex]?.textContent || '').trim();
}"""

# .mtrl-name div text (모든 페이지의 paper 표시 영역)
JS_GET_MTRL_NAME = """() => {
    const el = document.querySelector('.mtrl-name');
    return el ? el.textContent.trim() : null;
}"""

# PP카드 사이즈 — 페이지 표 형식. 표에서 "명함크기" 컬럼 추출
# (페이지 형식 fragile 하므로 일단 hidden input value 합성으로 대체)
JS_GET_PP_SIZE_HIDDEN = """() => {
    const hz = document.getElementById('ppr_cut_hz')?.value;
    const vt = document.getElementById('ppr_cut_vt')?.value;
    if (!hz || !vt) return null;
    return hz + 'mm × ' + vt + 'mm';
}"""


# ── 가격 파싱 ──

def parse_price(txt: Optional[str]) -> Optional[int]:
    if not txt:
        return None
    m = re.search(r"[\d,]+", txt.replace(" ", ""))
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


# ── 셋 유틸 ──

def js_set(page, sel_id: str, value) -> bool:
    try:
        return page.evaluate(JS_SET_SELECT, {"sel_id": sel_id, "value": value}) is True
    except Exception:
        return False


def js_get_select_text(page, sel_id: str) -> str:
    try:
        return page.evaluate(JS_GET_SELECT_TEXT, sel_id) or ""
    except Exception:
        return ""


def read_price(page, price_sel_id: str, after_trigger_ms: int) -> Optional[int]:
    page.evaluate(JS_TRIGGER_PRICE)
    page.wait_for_timeout(after_trigger_ms)
    txt = page.evaluate(JS_GET_PRICE, price_sel_id)
    return parse_price(txt or "")


# ── DOM 실측 (raw 추출 핵심) ──

def read_dom_state(page, sel: dict, page_type: str) -> dict:
    """현재 DOM 상태에서 raw 필드 추출. 합성 X, 표시값 그대로."""
    # paper_name = .mtrl-name div text (모든 페이지 일관)
    paper_name = page.evaluate(JS_GET_MTRL_NAME)
    if isinstance(paper_name, str):
        paper_name = paper_name.strip() or None

    # paper_weight_text = weight select text (페이지마다 다른 select id)
    weight_text = None
    if page_type == "mtrl_cd_pair":
        weight_text = js_get_select_text(page, sel.get("mtrl_cdw", "mtrl_cdw")) or None
    elif page_type == "mtrl_split":
        # 페이지마다 weight select id 다름 — 양쪽 후보 시도 (소량=mtrl_cd_02, 인디고=mtrl_02)
        for cand in (sel.get("mtrl_cd_02"), sel.get("mtrl_02")):
            if cand:
                v = js_get_select_text(page, cand)
                if v:
                    weight_text = v
                    break
    # page_fixed (일반명함) / mtrl_cd_only (PP) 는 별도 weight select 없음 → None

    # coating = coating_type select text
    coating_text = js_get_select_text(page, sel.get("coating_type", "coating_type")) or None

    # print_mode
    print_mode = js_get_select_text(page, sel.get("color_mode", "prn_clr_cn_gb")) or None

    # size — PP카드는 hidden input 합성, 그 외는 select text
    if page_type == "mtrl_cd_only":
        size = page.evaluate(JS_GET_PP_SIZE_HIDDEN)
    else:
        size = js_get_select_text(page, sel.get("size", "ppr_cut_tmp")) or None

    # qty = prn_sht_cn select value (정수)
    try:
        qty_val = page.evaluate(
            "(sid) => document.getElementById(sid)?.value || ''",
            sel.get("qty", "prn_sht_cn"),
        )
        qty = int(qty_val) if qty_val else None
    except (TypeError, ValueError):
        qty = None

    return {
        "paper_name": paper_name,
        "paper_weight_text": weight_text,
        "coating": coating_text,
        "print_mode": print_mode,
        "size": size,
        "qty": qty,
    }


# ── 브라우저 셋업 ──

def init_browser(pw, ctx: RunContext):
    """playwright 브라우저 + context + page 초기화."""
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


def goto_with_wait(page, url: str, timeouts: dict, ctx: RunContext, product: str) -> bool:
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=timeouts.get("page_goto_ms", 30000))
        page.wait_for_timeout(timeouts.get("after_goto_ms", 2500))
        return True
    except PwTimeout:
        ctx.log.event("fetch.fail", level="error",
                      product=product, error="page goto timeout")
        return False


# ── price guard ──

def price_with_retry(
    page, price_sel_id: str, qty: int,
    timeouts: dict, guard: dict,
) -> Optional[int]:
    """가격 읽기 + 비정상 저가 방어."""
    price = read_price(page, price_sel_id, timeouts.get("after_price_trigger_ms", 900))
    if price is None:
        return None
    floor = max(guard.get("floor_abs", 500), qty * guard.get("per_qty_multiplier", 3))
    if price < floor:
        page.evaluate(JS_TRIGGER_PRICE)
        page.wait_for_timeout(timeouts.get("retry_price_ms", 1500))
        price = read_price(page, price_sel_id, timeouts.get("after_price_trigger_ms", 900))
        if price is None or price < floor:
            return None
    return price


# ── page_type 별 paper 셋팅 ──

def set_paper_type_a(page, sel: dict, paper: dict, timeouts: dict) -> bool:
    """type-A page_fixed: 페이지 자체가 paper 고정. coating_type 만 셋팅."""
    coating_val = paper.get("coating_select_value", "")
    ok = js_set(page, sel["coating_type"], coating_val)
    page.wait_for_timeout(timeouts.get("after_select_ms", 500))
    return ok


def set_paper_type_b(page, sel: dict, paper: dict, timeouts: dict) -> bool:
    """type-B mtrl_cd + mtrl_cdw."""
    if not js_set(page, sel["mtrl_cd"], paper["mtrl_cd"]):
        return False
    page.wait_for_timeout(timeouts.get("after_select_ms", 500))
    if "mtrl_cdw" in paper:
        js_set(page, sel["mtrl_cdw"], paper["mtrl_cdw"])
        page.wait_for_timeout(timeouts.get("after_select_ms", 500))
    return True


def set_paper_type_c(page, sel: dict, paper: dict, timeouts: dict) -> bool:
    """type-C mtrl_cd only (PP카드). 평량/사이즈 select 없음."""
    ok = js_set(page, sel["mtrl_cd"], paper["mtrl_cd"])
    page.wait_for_timeout(timeouts.get("after_select_ms", 500))
    return ok


def set_paper_type_d(page, sel: dict, paper: dict, timeouts: dict) -> bool:
    """type-D 두 select (id 만 다름). target 에 sel_a/sel_b 키 명시."""
    sel_a = paper.get("sel_a") or sel.get("mtrl_cd_01") or sel.get("mtrl_01")
    sel_b = paper.get("sel_b") or sel.get("mtrl_cd_02") or sel.get("mtrl_02")
    if not js_set(page, sel_a, paper["paper_value"]):
        return False
    page.wait_for_timeout(timeouts.get("after_select_ms", 500))
    js_set(page, sel_b, paper["weight_value"])
    page.wait_for_timeout(timeouts.get("after_select_ms", 500))
    return True


# ── 공통 크롤 흐름 (paper 셋팅 후 색도×qty 순회 → RawItem yield) ──

def yield_items_for_paper(
    ctx: RunContext, page, t: dict, paper: dict, page_type: str,
    sel: dict, timeouts: dict, guard: dict, target_qtys: list[int],
) -> Iterator[RawItem]:
    """paper 셋팅 후 색도 × qty 순회. 각 조합에 대해 RawItem yield."""
    # PP카드 (type-C) 는 size select 가 없어 size 셋팅 단계 skip
    has_size_select = page_type != "mtrl_cd_only"

    color_modes = t.get("color_modes", [])
    for color in color_modes:
        if not js_set(page, sel.get("color_mode", "prn_clr_cn_gb"), color["value"]):
            continue
        page.wait_for_timeout(timeouts.get("after_color_ms", 400))

        # paper/color 변경 후 size 가 reset 되는 케이스 — 재셋팅
        if has_size_select and t.get("size_value"):
            js_set(page, sel.get("size", "ppr_cut_tmp"), t["size_value"])
            page.wait_for_timeout(200)

        for qty in target_qtys:
            if not js_set(page, sel.get("qty", "prn_sht_cn"), str(qty)):
                continue
            page.wait_for_timeout(timeouts.get("after_qty_ms", 300))
            price = price_with_retry(page, sel["price"], qty, timeouts, guard)
            if price is None:
                ctx.log.event("extract.warn", product=t["product_name"],
                              qty=qty, error="price read failed")
                continue
            yield build_item(page, t, paper, price, sel, page_type)


def build_item(page, t: dict, paper: dict, price: int, sel: dict, page_type: str) -> RawItem:
    """현재 DOM 상태로 RawItem 생성."""
    dom = read_dom_state(page, sel, page_type)

    options = {
        "config_paper_name_out": paper.get("paper_name_out"),
        "config_color_name": None,  # 호출 측에서 안 채움 (DOM color text 가 정답)
        "page_type": page_type,
    }
    # 추가 추적 필드
    for k in ("mtrl_cd", "mtrl_cdw", "coating_select_value",
              "paper_value", "weight_value", "actual_weight_g", "note"):
        if k in paper:
            options[f"config_{k}"] = paper[k]

    return RawItem(
        product=t["product_name"],
        category=t.get("category", t["product_name"]),
        paper_name=dom["paper_name"],
        paper_weight_text=dom["paper_weight_text"],
        coating=dom["coating"],
        print_mode=dom["print_mode"],
        size=dom["size"],
        qty=dom["qty"],
        price=price,
        price_vat_included=True,  # dtpia 가격은 VAT 포함
        url=t["url"],
        url_ok=True,
        options=options,
        match_as=paper.get("match_as"),
    )
