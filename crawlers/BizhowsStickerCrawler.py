"""
비즈하우스 도무송 스티커 크롤러.
config/sticker_targets.json bizhows 섹션 참조.

방식: selections 배열을 직접 조립 → selectedOptionList URL로 네비게이션 → DOM에서 가격 읽기.
selectOption API 순차 호출은 상태가 리셋되므로 사용하지 않음.

가격: VAT 별도 → normalize 단계에서 ×1.1 보정.
출력: output/bizhows_sticker_raw_now.json
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

COMPANY = "bizhows"
CATEGORY = "sticker"
BASE_URL = "https://www.bizhows.com/ko/v/option"

BLOCK_PATTERNS = [
    "**/f.clarity.ms/**", "**/analytics.tiktok.com/**", "**/channel.io/**",
    "**/criteo.net/**", "**/criteo.com/**", "**/google-analytics.com/**",
    "**/googletagmanager.com/**", "**/facebook.net/**", "**/facebook.com/tr/**",
    "**/doubleclick.net/**", "**/ads-twitter.com/**",
]

# 옵션 인덱스 (combination API optionList 순서)
IDX_DESIGN = 0  # 디자인 타입
IDX_SHAPE = 1   # 모양
IDX_SIZE = 2    # 사이즈
IDX_PAPER = 3   # 원단
IDX_QTY = 4     # 매수

# 사이즈 seq → mm 매핑 (원형)
SIZE_SEQ_MAP = {
    67096: ("지름 3.5cm", "35x35"),
    67097: ("지름 4.5cm", "45x45"),
    67098: ("지름 5.5cm", "55x55"),
    67099: ("지름 6.5cm", "65x65"),
    67100: ("지름 7cm", "70x70"),
    67101: ("지름 8cm", "80x80"),
    67102: ("지름 9cm", "90x90"),
    67103: ("지름 10cm", "100x100"),
}

# 타겟 사이즈 (normalized name)
TARGET_SIZES = {"45x45", "55x55", "65x65"}

JS_READ_PRICE = r"""() => {
    const qlEls = document.querySelectorAll('[data-f^="QL-"]');
    for (const ql of qlEls) {
        if (ql.textContent.trim() === '총 금액') {
            const qr = ql.parentElement?.querySelector('[data-f^="QR-"]');
            if (qr) return qr.textContent.trim();
        }
    }
    return null;
}"""


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("bizhows", [])


TARGETS = _load_targets()


def parse_price(txt: str) -> int | None:
    """가격 텍스트에서 최종 금액 추출. '15,000원(-20%)12,000원' → 12000"""
    if not txt:
        return None
    # 마지막 숫자,원 패턴 (할인가가 뒤에 옴)
    matches = re.findall(r"([\d,]+)원", txt)
    if matches:
        try:
            return int(matches[-1].replace(",", ""))
        except ValueError:
            pass
    return None


def crawl_all() -> list[dict]:
    items = []
    if not TARGETS:
        log.error("크롤 타겟 없음")
        return items

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        for pat in BLOCK_PATTERNS:
            context.route(pat, lambda r: r.abort())
        page = context.new_page()

        for t in TARGETS:
            log.info(f"  [{t['product_name']}]")

            nav_base = f"{BASE_URL}?code1={t['code1']}&code2={t['code2']}&code3={t['code3']}&mock={t['mock']}&from=product_list_001"
            page.goto(nav_base, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # combination API로 기본 selections 조회
            combo = page.evaluate(f"() => fetch('/api/v1/option/combination/{t['code1']}/{t['code2']}/{t['code3']}/{t['mock']}?selectOption={t['shape_seq']}').then(r=>r.json()).then(d=>d.data)")
            if not combo:
                log.error("    combination API fail")
                continue

            opt_list = combo["optionList"]
            base_selections = [o.get("selected") for o in opt_list]
            size_pov = opt_list[IDX_SIZE]["povList"]
            qty_seq = t["qty_seq"]

            log.info(f"    base_selections={base_selections}, sizes={len(size_pov)}종, papers={len(t['papers'])}종")

            # 사이즈 seq 필터링 (타겟만)
            target_size_seqs = []
            for sz_seq in size_pov:
                info = SIZE_SEQ_MAP.get(sz_seq)
                if info and info[1] in TARGET_SIZES:
                    target_size_seqs.append((sz_seq, info[0], info[1]))

            log.info(f"    target sizes: {[(s[1],s[2]) for s in target_size_seqs]}")

            # 용지별 × 사이즈별 크롤링 — selections 직접 조립
            for paper in t["papers"]:
                paper_seq = paper["seq"]
                paper_name = paper["name"]
                log.info(f"    용지: {paper_name} (seq={paper_seq})")

                for sz_seq, sz_label, sz_normalized in target_size_seqs:
                    sels = list(base_selections)
                    sels[IDX_SIZE] = sz_seq
                    sels[IDX_PAPER] = paper_seq
                    sels[IDX_QTY] = qty_seq
                    sel_str = ",".join(str(s) for s in sels)

                    nav_url = f"{nav_base}&selectedOptionList={sel_str}"
                    try:
                        page.goto(nav_url, wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_function("() => document.body.innerText.includes('총 금액')", timeout=10000)
                        page.wait_for_timeout(500)
                    except PwTimeout:
                        log.warning(f"      {sz_normalized}: timeout")
                        continue

                    price_txt = page.evaluate(JS_READ_PRICE)
                    price = parse_price(price_txt or "")

                    if price and price > 0:
                        items.append({
                            "product": t["product_name"],
                            "category": "스티커",
                            "paper_name": paper_name,
                            "coating": "비코팅" if "무코팅" in paper_name else ("유광코팅" if "코팅" in paper_name else ""),
                            "print_mode": "단면칼라",
                            "size": sz_normalized,
                            "qty": 1000,
                            "price": price,
                            "price_vat_included": False,
                            "url": nav_url,
                            "url_ok": True,
                            "options": {"shape": "원형", "ea_per_sheet": 1},
                        })
                        log.info(f"      {paper_name} | {sz_normalized} -> {price:,} (VAT별도)")
                    else:
                        log.warning(f"      {paper_name} | {sz_normalized}: price fail | raw={price_txt}")

        browser.close()

    log.info(f"=== 완료: {len(items)}건 ===")
    return items


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
