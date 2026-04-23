"""
성원애드피아 봉투 크롤러.
config/envelope_targets.json swadpia 섹션 참조.

페이지: /goods/goods_view/CEV1000/GEV1001 (대중소봉투)
조건:
  - bongto_kind=CE1 (규격)
  - bongto_type: CE101(대봉투 330x245) / CE103(9절 중봉투 260x190)
  - cal_line=BDC40 (단면칼라 전용, 단면흑백 옵션 없음 → 결측)
  - paper_qty=1000

용지 선택 3단: paper_kind → paper_type → paper_code
  각 paper_kind 선택 후 paper_type 드롭다운 옵션 dump → paper_code 순회.

가격: #print_estimate_tot (VAT 포함).
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

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "envelope_targets.json"
COMPANY = "swadpia"
CATEGORY = "envelope"

BLOCK_PATTERNS = [
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**", "**/doubleclick.net/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/analytics.tiktok.com/**",
]

_RE_TOTAL = re.compile(r"총\s*합계금액\s*[:：]?\s*[\\￦₩]?\s*([\d,]+)\s*원")

JS_GET_PRICE = r"""() => {
    const e = document.querySelector('#print_estimate_tot');
    if (!e) return null;
    return e.textContent.replace(/\s+/g, ' ').trim();
}"""

JS_DUMP_SELECT = """(name) => {
    const s = document.querySelector(`select[name="${name}"]`);
    if (!s) return [];
    return [...s.options].map(o => ({value: o.value, text: o.textContent.trim()}));
}"""


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("swadpia", [])


TARGETS = _load_targets()


def parse_total_price(txt: str) -> int | None:
    if not txt:
        return None
    m = _RE_TOTAL.search(txt)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


class SwadpiaEnvelopeCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.items: list[dict] = []

    def _init_browser(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900}, locale="ko-KR",
        )
        for pat in BLOCK_PATTERNS:
            context.route(pat, lambda r: r.abort())
        context.on("dialog", lambda d: d.dismiss())
        return browser, context

    def _set_select(self, page, name: str, value: str):
        page.evaluate(f"""() => {{
            const s = document.querySelector('select[name="{name}"]');
            if (s) {{
                s.value = '{value}';
                s.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
        }}""")

    def _dump_select(self, page, name: str):
        return page.evaluate(JS_DUMP_SELECT, name)

    def _read_env_state(self, page, pm_field: str) -> dict:
        """현재 DOM 의 봉투 옵션 상태 — raw 필드 소스."""
        raw = page.evaluate(
            """(pmField) => {
                const selText = (name) => {
                    const el = document.querySelector(`select[name="${name}"]`);
                    if (!el || el.selectedIndex < 0) return '';
                    return (el.options[el.selectedIndex]?.textContent || '').trim();
                };
                const selVal = (name) => {
                    const el = document.querySelector(`select[name="${name}"]`);
                    return el ? (el.value || '') : '';
                };
                return {
                    pm_text:   selText(pmField),
                    size_text: selText('bongto_type'),
                    qty_val:   selVal('paper_qty'),
                };
            }""",
            pm_field,
        ) or {}
        try:
            qty = int(raw.get("qty_val") or "")
        except (TypeError, ValueError):
            qty = 0
        return {
            "print_mode": (raw.get("pm_text") or "").strip(),
            "size":       (raw.get("size_text") or "").strip(),
            "qty":        qty,
        }

    def _crawl_product(self, page, t: dict):
        url = t["url"]
        print_mode = t.get("print_mode", "단면칼라")
        pm_field = t.get("print_mode_field", "cal_line")
        pm_value = t.get("print_mode_value", "BDC40")
        log.info(f"  [{t['product_name']}] {url} → {print_mode} ({pm_field}={pm_value})")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except PwTimeout:
            log.warning("    page timeout")

        # 인쇄도수 + 수량 먼저 설정 (bongto_kind/type은 사이즈 루프에서)
        self._set_select(page, pm_field, pm_value)
        page.wait_for_timeout(400)
        self._set_select(page, "paper_qty", "1000")
        page.wait_for_timeout(400)

        skip_pk_loop = t.get("skip_paper_kind_loop", False)

        for sz in t["sizes"]:
            size_canonical = sz["canonical"]
            bongto_kind = sz.get("bongto_kind", "CE1")
            bongto_type = sz["bongto_type"]
            log.info(f"    [size] {size_canonical} bongto_kind={bongto_kind} bongto_type={bongto_type}")
            self._set_select(page, "bongto_kind", bongto_kind)
            page.wait_for_timeout(500)
            self._set_select(page, "bongto_type", bongto_type)
            page.wait_for_timeout(700)

            if skip_pk_loop:
                # paper_kind/paper_type UI 장식인 페이지(CEV5000 기성형 등) — paper_code만 직접 순회
                paper_codes = self._dump_select(page, "paper_code")
                for pc in paper_codes:
                    pc_val, pc_text = pc["value"], pc["text"]
                    if not pc_val:
                        continue
                    self._set_select(page, "paper_code", pc_val)
                    page.wait_for_timeout(700)

                    txt = page.evaluate(JS_GET_PRICE)
                    price = parse_total_price(txt or "")
                    if price and price > 0:
                        dom = self._read_env_state(page, pm_field)
                        self.items.append({
                            "product":        t["product_name"],
                            "category":       "봉투",
                            "paper_name":     pc_text or None,
                            "paper_kind_raw": None,
                            "paper_type_raw": None,
                            "paper_code_raw": pc_text or None,
                            # swadpia 봉투 페이지는 coating UI 없음 → null
                            "coating":        None,
                            "print_mode":     dom["print_mode"] or None,
                            "size":           dom["size"]       or None,
                            "size_raw":       sz.get("label", ""),
                            "qty":            dom["qty"]        or None,
                            "price":          price,
                            "price_vat_included": True,
                            "url":            url,
                            "url_ok":         True,
                            "options": {
                                "size_canonical":    size_canonical,
                                "config_print_mode": print_mode,
                                "config_qty":        1000,
                            },
                        })
                        log.info(f"      ✓ DOM={pc_text} | {dom['print_mode']} | {dom['size']} → {price:,}")
                continue

            # 기본: paper_kind → paper_type → paper_code 3단 순회
            paper_kinds = self._dump_select(page, "paper_kind")
            for pk in paper_kinds:
                pk_val, pk_text = pk["value"], pk["text"]
                if not pk_val:
                    continue
                log.info(f"      [paper_kind] {pk_text} ({pk_val})")
                self._set_select(page, "paper_kind", pk_val)
                page.wait_for_timeout(800)

                paper_types = self._dump_select(page, "paper_type")
                for pt in paper_types:
                    pt_val, pt_text = pt["value"], pt["text"]
                    if not pt_val:
                        continue
                    self._set_select(page, "paper_type", pt_val)
                    page.wait_for_timeout(600)

                    paper_codes = self._dump_select(page, "paper_code")
                    for pc in paper_codes:
                        pc_val, pc_text = pc["value"], pc["text"]
                        if not pc_val:
                            continue
                        self._set_select(page, "paper_code", pc_val)
                        page.wait_for_timeout(700)

                        txt = page.evaluate(JS_GET_PRICE)
                        price = parse_total_price(txt or "")
                        if price and price > 0:
                            # paper_type(모조지/페스티발/크라프트 등) + paper_code(세부 색상·평량)
                            # 결합해야 normalize aliases에서 canonical 매칭 가능
                            paper_name_raw = f"{pt_text} {pc_text}".strip() if pt_text else pc_text
                            dom = self._read_env_state(page, pm_field)
                            self.items.append({
                                "product":        t["product_name"],
                                "category":       "봉투",
                                "paper_name":     paper_name_raw or None,
                                "paper_kind_raw": pk_text or None,
                                "paper_type_raw": pt_text or None,
                                "paper_code_raw": pc_text or None,
                                "coating":        None,
                                "print_mode":     dom["print_mode"] or None,
                                "size":           dom["size"]       or None,
                                "size_raw":       sz.get("label", ""),
                                "qty":            dom["qty"]        or None,
                                "price":          price,
                                "price_vat_included": True,
                                "url":            url,
                                "url_ok":         True,
                                "options": {
                                    "size_canonical":    size_canonical,
                                    "config_print_mode": print_mode,
                                    "config_qty":        1000,
                                },
                            })
                            log.info(f"        ✓ DOM={paper_name_raw} | {dom['print_mode']} | {dom['size']} → {price:,}")

    def run(self):
        log.info(f"=== 성원애드피아 봉투 크롤링 시작 ({len(TARGETS)}종) ===")
        if not TARGETS:
            log.error("크롤 타겟 없음")
            return
        start = time.time()
        with sync_playwright() as pw:
            browser, context = self._init_browser(pw)
            page = context.new_page()
            for i, t in enumerate(TARGETS, 1):
                log.info(f"[{i}/{len(TARGETS)}] {t['product_name']}")
                try:
                    self._crawl_product(page, t)
                except Exception as e:
                    log.error(f"  error: {e}", exc_info=True)
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


def crawl_all() -> list[dict]:
    c = SwadpiaEnvelopeCrawler(headless=True)
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
