"""
디티피아 도무송 스티커 크롤러.
config/sticker_targets.json dtpia 섹션 참조.

DtpiaCardCrawler 패턴 기반 Playwright DOM 조작.
가격: #est_scroll_total_am (VAT 포함 합계)

조건:
  - 초강접스티커(아트지 90g)
  - 유광코팅
  - 자유형 도무송 원형
  - 사이즈: 40x40 ~ 90x90 (재단사이즈 직접 입력)
  - 수량: 1000장

출력: output/dtpia_sticker_raw_now.json
"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sticker_targets.json"

COMPANY = "dtpia"
CATEGORY = "sticker"

BLOCK_PATTERNS = [
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**", "**/doubleclick.net/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/analytics.tiktok.com/**",
]


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("dtpia", [])


TARGETS = _load_targets()


JS_SET_SELECT = """({sel_id, value}) => {
    const el = document.getElementById(sel_id);
    if (!el) return 'NO_EL:' + sel_id;
    const wantedVal = String(value);
    const opt = [...el.options].find(o => o.value === wantedVal);
    if (!opt && wantedVal !== '') return 'NO_OPT:' + wantedVal + ' opts=' + [...el.options].map(o=>o.value).join(',');
    el.value = wantedVal;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""

JS_SET_SELECT_BY_TEXT = """({sel_id, text}) => {
    const el = document.getElementById(sel_id);
    if (!el) return 'NO_EL:' + sel_id;
    const opt = [...el.options].find(o => o.textContent.includes(text));
    if (!opt) return 'NO_OPT:' + text + ' opts=' + [...el.options].slice(0,10).map(o=>o.textContent.trim()).join('|');
    el.value = opt.value;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return opt.textContent.trim();
}"""

JS_TRIGGER_PRICE = """() => {
    if (typeof callPrice === 'function') {
        try { callPrice(); } catch(e) {}
    }
}"""

JS_GET_PRICE = """() => {
    const el = document.getElementById('est_scroll_total_am');
    return el ? el.textContent.trim() : null;
}"""

JS_DUMP_SELECTS = """() => {
    return [...document.querySelectorAll('select')].map(s => ({
        id: s.id, name: s.name,
        opts: [...s.options].slice(0,15).map(o => o.value + '|' + o.textContent.trim())
    }));
}"""

JS_DUMP_INPUTS = """() => {
    return [...document.querySelectorAll('input[type="text"], input[type="number"]')].map(i => ({
        id: i.id, name: i.name, value: i.value, placeholder: i.placeholder
    }));
}"""


def parse_price(txt: str) -> int | None:
    if not txt:
        return None
    m = re.search(r"[\d,]+", txt.replace(" ", ""))
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


def _set_input(page, input_id: str, value: str):
    """input 필드에 값 입력 (nativeInputValueSetter 사용)."""
    page.evaluate(f"""() => {{
        const el = document.getElementById('{input_id}');
        if (!el) return;
        const nativeSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        nativeSetter.call(el, '{value}');
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
    }}""")


def _set_input_by_name(page, name: str, value: str):
    page.evaluate(f"""() => {{
        const el = document.querySelector('input[name="{name}"]');
        if (!el) return;
        const nativeSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        nativeSetter.call(el, '{value}');
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
    }}""")


class DtpiaStickerCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.items: list[dict] = []

    def _init_browser(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        for pat in BLOCK_PATTERNS:
            context.route(pat, lambda r: r.abort())
        context.on("dialog", lambda d: d.dismiss())
        return browser, context

    def _crawl_product(self, page, t: dict):
        url = t["url"]
        log.info(f"  [{t['product_name']}] {url}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
        except PwTimeout:
            log.warning("    page timeout, continuing")

        # DOM 구조 파악
        selects = page.evaluate(JS_DUMP_SELECTS)
        inputs = page.evaluate(JS_DUMP_INPUTS)

        outdir = Path(__file__).resolve().parent.parent / "output"
        with open(outdir / "dtpia_sticker_dom.json", "w", encoding="utf-8") as f:
            json.dump({"selects": selects, "inputs": inputs}, f, ensure_ascii=False, indent=2)
        log.info(f"    DOM dump saved")

        for s in selects:
            log.info(f"    [select] id={s['id']} name={s['name']}")
            for o in s['opts'][:5]:
                log.info(f"      {o}")

        for inp in inputs[:10]:
            log.info(f"    [input] id={inp['id']} name={inp['name']} val={inp['value']}")

        # 공통 옵션 설정
        coating = t["coatings"][0] if t.get("coatings") else {"code": "1", "name": "유광코팅"}

        # 자유형 도무송 선택
        page.evaluate(JS_SET_SELECT, {"sel_id": "sticker_type", "value": "ts"})
        page.wait_for_timeout(1000)

        # 칼선 형태: A. 원형 도무송
        page.evaluate(JS_SET_SELECT, {"sel_id": "kal_type_tmp", "value": "A|1"})
        page.wait_for_timeout(500)

        # 수량
        page.evaluate(JS_SET_SELECT, {"sel_id": "prn_sht_cn", "value": str(t["qtys"][0])})
        page.wait_for_timeout(500)

        # 코팅
        page.evaluate(JS_SET_SELECT, {"sel_id": "coating_type", "value": coating["code"]})
        page.wait_for_timeout(500)

        log.info(f"    공통 옵션 설정 완료")

        # 용지별 × 사이즈별 크롤링
        for paper in t["papers"]:
            # 용지 선택 (코드 기반)
            res = page.evaluate(JS_SET_SELECT, {"sel_id": "mtrl_cd", "value": paper["code"]})
            if res is not True:
                log.warning(f"    용지 선택 실패: {paper['name']} ({paper['code']}): {res}")
                continue
            log.info(f"    용지: {paper['name']}")
            page.wait_for_timeout(1000)

            for size_info in t["sizes"]:
                size_mm = size_info["mm"]
                size_label = size_info["name"]

                size_set = page.evaluate(f"""() => {{
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    const fields = [
                        ['kal_hz', '{size_mm}'], ['kal_vt', '{size_mm}'],
                        ['ppr_cut_hz', '{size_mm}'], ['ppr_cut_vt', '{size_mm}'],
                        ['wk_hz', '{size_mm}'], ['wk_vt', '{size_mm}']
                    ];
                    let count = 0;
                    for (const [id, val] of fields) {{
                        const el = document.getElementById(id);
                        if (el) {{
                            nativeSetter.call(el, val);
                            el.dispatchEvent(new Event('change', {{bubbles: true}}));
                            count++;
                        }}
                    }}
                    return {{ok: count > 0, count: count}};
                }}""")

                if not size_set or not size_set.get("ok"):
                    log.warning(f"      {size_label}: size input fail")
                    continue

                page.wait_for_timeout(500)
                page.evaluate(JS_TRIGGER_PRICE)
                page.wait_for_timeout(1500)

                txt = page.evaluate(JS_GET_PRICE)
                price = parse_price(txt or "")

                if price and price > 0:
                    self.items.append({
                        "product": t["product_name"],
                        "category": "스티커",
                        "paper_name": paper["name"],
                        "coating": coating["name"],
                        "print_mode": "자유형 도무송",
                        "size": size_label,
                        "qty": t["qtys"][0],
                        "price": price,
                        "price_vat_included": True,
                        "url": url,
                        "url_ok": True,
                        "options": {"shape": "원형", "ea_per_sheet": 1},
                    })
                    log.info(f"      {paper['name']} | {size_label} -> {price:,}")
                else:
                    log.warning(f"      {paper['name']} | {size_label}: price fail")

    def run(self):
        log.info(f"=== 디티피아 스티커 크롤링 시작 ({len(TARGETS)}종) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음")
            return
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            for i, t in enumerate(TARGETS, 1):
                log.info(f"[{i}/{len(TARGETS)}]")
                try:
                    self._crawl_product(page, t)
                except Exception as e:
                    log.error(f"  crawl error: {e}")
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


def crawl_all() -> list[dict]:
    c = DtpiaStickerCrawler(headless=True)
    c.run()
    return c.items


def save(items: list[dict]):
    base = Path(__file__).resolve().parent.parent
    outdir = base / "output"
    outdir.mkdir(exist_ok=True)
    raw_now = outdir / f"{COMPANY}_{CATEGORY}_raw_now.json"
    output = {
        "company": COMPANY,
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": items,
    }
    with open(raw_now, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"saved: {raw_now} ({len(items)} items)")


if __name__ == "__main__":
    items = crawl_all()
    save(items)
