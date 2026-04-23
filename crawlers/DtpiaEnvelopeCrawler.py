"""
디티피아 봉투 크롤러.
config/envelope_targets.json dtpia 섹션 참조.

두 제품 페이지를 각각 크롤링:
  1. 칼라봉투 (/Order/Envelope/Standard.aspx)
     - sdiv_cd: A501(대봉투 4절) / A302(9절봉투)
     - jijil_gb × mtrl_cd 2단 용지 선택 (지질 계열 선택 시 mtrl_cd 리필)
     - prn_clr_cn_gb=4 (칼라4도), prn_sht_cn=1000, 아래뚜껑 인쇄 없음, 일반가공
  2. 흑백 기성봉투 (/Order/Envelope/Master.aspx)
     - sdiv_cd: AMA2(대봉투만, 9절 없음)
     - en_category=A(서류봉투) → en_type이 '4절 크라프트 98g' 등 용지 묶음 선택
     - prn_clr_cn_gb=1 (단면1도=흑백), prn_sht_cn=1000

가격: #est_scroll_total_am (VAT 포함 합계)
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
COMPANY = "dtpia"
CATEGORY = "envelope"

BLOCK_PATTERNS = [
    "**/google-analytics.com/**", "**/googletagmanager.com/**",
    "**/facebook.net/**", "**/facebook.com/tr/**", "**/doubleclick.net/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/analytics.tiktok.com/**",
]


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

JS_DUMP_SELECT = """(sel_id) => {
    const el = document.getElementById(sel_id);
    if (!el) return [];
    return [...el.options].map(o => ({value: o.value, text: o.textContent.trim()}));
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


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("dtpia", [])


TARGETS = _load_targets()


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


JS_READ_ENV_STATE = """() => {
    const selText = (id) => {
        const el = document.getElementById(id);
        if (!el || el.selectedIndex < 0) return '';
        return (el.options[el.selectedIndex]?.textContent || '').trim();
    };
    const selVal = (id) => {
        const el = document.getElementById(id);
        return el ? (el.value || '') : '';
    };
    return {
        size_text:    selText('sdiv_cd'),
        qty_val:      selVal('prn_sht_cn'),
        clr_text:     selText('prn_clr_cn_gb'),
    };
}"""


def read_env_state(page) -> dict:
    """dtpia 봉투 페이지 DOM 실측 (Standard.aspx / Master.aspx 공통)."""
    raw = page.evaluate(JS_READ_ENV_STATE) or {}
    try:
        qty = int(raw.get("qty_val") or "")
    except (TypeError, ValueError):
        qty = 0
    return {
        "size":       (raw.get("size_text") or "").strip(),
        "qty":        qty,
        "color_text": (raw.get("clr_text") or "").strip(),
    }


class DtpiaEnvelopeCrawler:
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

    # ── 칼라봉투 (Standard.aspx) ──────────────────────────────
    def _crawl_standard(self, page, t: dict):
        url = t["url"]
        print_mode = t["print_mode"]
        log.info(f"  [{t['product_name']}] {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except PwTimeout:
            log.warning("    page timeout")

        # 공통 옵션: 칼라4도, 1000매, 아래뚜껑 인쇄 없음, 일반가공
        page.evaluate(JS_SET_SELECT, {
            "sel_id": "prn_clr_cn_gb",
            "value": t.get("color_field", {}).get("prn_clr_cn_gb", "4"),
        })
        page.wait_for_timeout(300)
        page.evaluate(JS_SET_SELECT, {
            "sel_id": "prn_sht_cn", "value": t.get("qty_target_value", "1000"),
        })
        page.wait_for_timeout(300)
        page.evaluate(JS_SET_SELECT, {"sel_id": "envp_back_cap_prt_yn", "value": "N"})
        page.wait_for_timeout(300)
        page.evaluate(JS_SET_SELECT, {"sel_id": "cover_tomson", "value": ""})
        page.wait_for_timeout(300)

        for sz in t["sizes"]:
            size_canonical = sz["canonical"]
            sdiv_cd = sz["sdiv_cd"]
            log.info(f"    [size] {size_canonical} sdiv_cd={sdiv_cd}")
            r = page.evaluate(JS_SET_SELECT, {"sel_id": "sdiv_cd", "value": sdiv_cd})
            if r is not True:
                log.warning(f"      sdiv_cd 선택 실패: {r}")
                continue
            page.wait_for_timeout(700)

            # jijil_gb 전수조사
            jijils = page.evaluate(JS_DUMP_SELECT, "jijil_gb")
            for jj in jijils:
                jj_val, jj_text = jj["value"], jj["text"]
                if not jj_val:
                    continue
                log.info(f"      [jijil] {jj_text} ({jj_val})")
                r1 = page.evaluate(JS_SET_SELECT, {"sel_id": "jijil_gb", "value": jj_val})
                if r1 is not True:
                    log.warning(f"        jijil_gb 선택 실패: {r1}")
                    continue
                page.wait_for_timeout(600)

                mtrls = page.evaluate(JS_DUMP_SELECT, "mtrl_cd")
                for mm in mtrls:
                    mm_val, mm_text = mm["value"], mm["text"]
                    if not mm_val:
                        continue
                    r2 = page.evaluate(JS_SET_SELECT, {"sel_id": "mtrl_cd", "value": mm_val})
                    if r2 is not True:
                        continue
                    page.wait_for_timeout(400)
                    page.evaluate(JS_TRIGGER_PRICE)
                    page.wait_for_timeout(1500)
                    txt = page.evaluate(JS_GET_PRICE)
                    price = parse_price(txt or "")
                    if price and price > 0:
                        # paper_family(지질계열) + mtrl_cd(세부 용지·평량) 결합 → normalize aliases 매칭용 raw
                        paper_name_raw = f"{jj_text} {mm_text}".strip() if jj_text else mm_text
                        dom = read_env_state(page)
                        self.items.append({
                            "product":        t["product_name"],
                            "category":       "봉투",
                            "paper_name":     paper_name_raw or None,
                            "paper_family":   jj_text or None,
                            "paper_code_raw": mm_text or None,
                            # dtpia 봉투 페이지는 coating UI 노출 없음 → null
                            "coating":        None,
                            "print_mode":     dom["color_text"] or None,
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
                        log.info(f"        ✓ DOM={jj_text} {mm_text} | {dom['color_text']} | {dom['size']} → {price:,}")
                    else:
                        log.info(f"        - {mm_text}: no price")

    # ── 흑백 기성봉투 (Master.aspx) ──────────────────────────
    def _crawl_master(self, page, t: dict):
        url = t["url"]
        print_mode = t["print_mode"]
        log.info(f"  [{t['product_name']}] {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except PwTimeout:
            log.warning("    page timeout")

        # 단면1도(흑백), 1000매
        page.evaluate(JS_SET_SELECT, {
            "sel_id": "prn_clr_cn_gb",
            "value": t.get("color_field", {}).get("prn_clr_cn_gb", "1"),
        })
        page.wait_for_timeout(300)
        page.evaluate(JS_SET_SELECT, {
            "sel_id": "prn_sht_cn", "value": t.get("qty_target_value", "1000"),
        })
        page.wait_for_timeout(300)

        for enc in t.get("en_category", []):
            enc_val = enc["value"]
            log.info(f"    [en_category] {enc.get('label', '')} ({enc_val})")
            r = page.evaluate(JS_SET_SELECT, {"sel_id": "en_category", "value": enc_val})
            if r is not True:
                log.warning(f"      en_category 선택 실패: {r}")
                continue
            page.wait_for_timeout(700)

            # en_type 전수조사
            types = page.evaluate(JS_DUMP_SELECT, "en_type")

            for sz in t["sizes"]:
                size_canonical = sz["canonical"]
                sdiv_cd = sz["sdiv_cd"]
                page.evaluate(JS_SET_SELECT, {"sel_id": "sdiv_cd", "value": sdiv_cd})
                page.wait_for_timeout(400)

                for ty in types:
                    ty_val, ty_text = ty["value"], ty["text"]
                    if not ty_val:
                        continue
                    r2 = page.evaluate(JS_SET_SELECT, {"sel_id": "en_type", "value": ty_val})
                    if r2 is not True:
                        continue
                    page.wait_for_timeout(400)
                    page.evaluate(JS_TRIGGER_PRICE)
                    page.wait_for_timeout(1500)
                    txt = page.evaluate(JS_GET_PRICE)
                    price = parse_price(txt or "")
                    if price and price > 0:
                        # en_type text 예: "4절 초특대형봉투 (크라프트 98g)" → 괄호 속 용지명 추출
                        paper_m = re.search(r"\(([^)]+)\)\s*$", ty_text)
                        paper_raw = paper_m.group(1).strip() if paper_m else ty_text
                        dom = read_env_state(page)
                        self.items.append({
                            "product":    t["product_name"],
                            "category":   "봉투",
                            "paper_name": paper_raw or None,
                            "coating":    None,
                            "print_mode": dom["color_text"] or None,
                            "size":       dom["size"]       or None,
                            "size_raw":   sz.get("label", ""),
                            "qty":        dom["qty"]        or None,
                            "price":      price,
                            "price_vat_included": True,
                            "url":        url,
                            "url_ok":     True,
                            "options": {
                                "en_type_raw":       ty_text,
                                "size_canonical":    size_canonical,
                                "config_print_mode": print_mode,
                                "config_qty":        1000,
                            },
                        })
                        log.info(f"      ✓ DOM={paper_raw} | {dom['color_text']} | {dom['size']} → {price:,}")

    def run(self):
        log.info(f"=== 디티피아 봉투 크롤링 시작 ({len(TARGETS)}종) ===")
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
                    url = t.get("url", "")
                    if "Standard.aspx" in url:
                        self._crawl_standard(page, t)
                    elif "Master.aspx" in url:
                        self._crawl_master(page, t)
                    else:
                        log.warning(f"  알 수 없는 페이지: {url}")
                except Exception as e:
                    log.error(f"  error: {e}", exc_info=True)
            browser.close()
        elapsed = time.time() - start
        log.info(f"=== 완료: {len(self.items)}건, {elapsed:.1f}초 ===")


def crawl_all() -> list[dict]:
    c = DtpiaEnvelopeCrawler(headless=True)
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
