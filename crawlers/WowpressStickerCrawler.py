#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WowpressStickerCrawler.py
와우프레스 도무송 스티커 가격 크롤러

대상: https://wowpress.co.kr/ordr/prod/dets?ProdNo=40014
조건: 강접아트지(유광코팅) 80g / 단면칼라 / 1000매
사이즈: 40x40, 50x50, 60x60 (프리셋), 70x70, 80x80, 90x90 (비규격)

실행:
    python crawlers/WowpressStickerCrawler.py          # headless=True
    python crawlers/WowpressStickerCrawler.py --show   # headless=False (디버깅)
"""

import os
import json
import time
from pathlib import Path
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, Page

# ─── 상수 ──────────────────────────────────────────────────────────────────────
COMPANY    = "wowpress"
CATEGORY   = "sticker"
URL        = "https://wowpress.co.kr/ordr/prod/dets?ProdNo=40617"
OUTPUT_DIR = "output"

# pdata_00_sizeno 기준 원형 프리셋 옵션값
# DOM 확인: 원형45=5517, 원형55=5519, 원형65=5521
PRESET_SIZES: dict[int, str] = {
    45: "5517",
    55: "5519",
    65: "5521",
}

# 비규격 size value
IRREGULAR_SIZE_VALUE = "6066"

# 비규격 작업 여백 (+5mm)
BLEED = 5

# 크롤링 대상 사이즈
TARGET_SIZES = [45, 55, 65, 75, 85, 95]

# ─── 공통 유틸 ─────────────────────────────────────────────────────────────────

def ensure_output_dir() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def set_select_and_trigger(page: Page, selector: str, value: str) -> None:
    """
    select[selector].value = value 후 onchange 속성 JS 실행.
    와우프레스의 모든 select는 onchange에 직접 JS 함수가 바인딩되어 있어
    value만 바꾸면 가격이 갱신되지 않으므로 반드시 eval이 필요함.
    """
    page.evaluate(
        """([sel, val]) => {
            const el = document.querySelector(sel);
            if (!el) return;
            el.value = val;
            const oc = el.getAttribute('onchange');
            if (oc) {
                // 'this' 바인딩을 위해 Function 생성 후 call
                try { (new Function('event', oc)).call(el, null); }
                catch(e) { console.warn('onchange eval error:', e); }
            }
        }""",
        [selector, value],
    )


def get_price(page: Page) -> int:
    """#od_00_totalcost 텍스트에서 숫자만 추출 (VAT 포함 총 결제금액)"""
    try:
        text = page.evaluate(
            "() => (document.getElementById('od_00_totalcost') || {}).textContent || ''"
        )
        cleaned = re.sub(r"[^0-9]", "", str(text))
        return int(cleaned) if cleaned else 0
    except Exception:
        return 0


def save_screenshot(page: Page, name: str) -> None:
    path = os.path.join(OUTPUT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  [screenshot] {path}")


# ─── 기본 옵션 설정 ─────────────────────────────────────────────────────────────

def setup_base_options(page: Page) -> None:
    """
    용지(첫 번째 옵션) / 단면칼라 / 1000매 / 1건 고정.
    reqMdmDetail/getTemplate 호출 후 이 옵션들이 초기화될 수 있어
    사이즈 변경 후에도 재호출해야 함.
    paperno3/paperno4는 페이지마다 다르므로 첫 번째 옵션을 자동 선택.
    """
    # 용지 paperno3: 첫 번째 옵션 선택
    page.evaluate(r"""() => {
        const sel = document.getElementById('spdata_00_paperno3');
        if (sel && sel.options.length > 0) {
            sel.value = sel.options[0].value;
            const oc = sel.getAttribute('onchange');
            if (oc) { try { (new Function('event', oc)).call(sel, null); } catch(e) {} }
        }
    }""")
    time.sleep(1.5)

    # 용지 paperno4: 첫 번째 옵션 선택
    page.evaluate(r"""() => {
        const sel = document.getElementById('spdata_00_paperno4');
        if (sel && sel.options.length > 0) {
            sel.value = sel.options[0].value;
            const oc = sel.getAttribute('onchange');
            if (oc) { try { (new Function('event', oc)).call(sel, null); } catch(e) {} }
        }
    }""")
    time.sleep(1.5)

    # 색도 = 단면칼라 = 302
    set_select_and_trigger(page, "#pdata_00_colorno", "302")
    time.sleep(1.0)

    # 수량 = 1000매
    set_select_and_trigger(page, "#spdata_00_ordqty", "1000")
    time.sleep(1.0)

    # 건수 = 1건
    set_select_and_trigger(page, "#pdata_00_ordcnt", "1")
    time.sleep(1.0)


# ─── 프리셋 사이즈 크롤링 (40 / 50 / 60) ──────────────────────────────────────

def crawl_preset_size(page: Page, size_mm: int) -> dict:
    """
    pdata_00_sizeno select에서 원형XX 값 선택.
    onchange: reqMdmDetail('Size', value, '0', 'hdata_00_sizeno')
    → AJAX로 페이지 일부 갱신하므로 4초 대기 후 옵션 재설정.
    """
    size_value = PRESET_SIZES[size_mm]
    size_str   = f"{size_mm}x{size_mm}"
    print(f"  [preset] 원형{size_mm}  sizeno={size_value}")

    page.evaluate(
        """([val]) => {
            const el = document.getElementById('pdata_00_sizeno');
            if (!el) return;
            el.value = val;
            // onchange: reqMdmDetail('Size', this.value, '0', 'hdata_00_sizeno')
            if (typeof reqMdmDetail === 'function') {
                reqMdmDetail('Size', val, '0', 'hdata_00_sizeno');
            }
        }""",
        [size_value],
    )
    time.sleep(4)  # AJAX 완료 대기

    # 사이즈 변경 후 옵션 초기화 방지 → 재설정
    setup_base_options(page)
    time.sleep(2)

    price = get_price(page)
    if price == 0:
        print(f"  [WARN] 가격 0원 - 스크린샷 저장")
        save_screenshot(page, f"error_preset_{size_mm}")

    print(f"    가격: {price:,}원")
    return {"price": price}


# ─── 비규격 사이즈 크롤링 ─────────────────────────────────────────────────────

def crawl_irregular_size(page: Page, size_mm: int) -> dict:
    """
    비규격(6066) 선택 → hdata_00_sizeno_x/y/xx/yy 직접 입력 → fnOrdSummary() 호출.
    작업 사이즈 = 재단 + BLEED(5mm).
    """
    size_str  = f"{size_mm}x{size_mm}"
    work_size = size_mm + BLEED
    print(f"  [irregular] {size_str}  작업={work_size}x{work_size}")

    # 1) 비규격 선택 + reqMdmDetail 트리거
    page.evaluate(
        """() => {
            const el = document.getElementById('pdata_00_sizeno');
            if (!el) return;
            el.value = '6066';
            if (typeof reqMdmDetail === 'function') {
                reqMdmDetail('Size', '6066', '0', 'hdata_00_sizeno');
            }
        }"""
    )
    time.sleep(3)

    # 2) 재단/작업 사이즈 입력
    page.evaluate(
        """([cut, work]) => {
            const set = (id, v) => {
                const el = document.getElementById(id);
                if (el) el.value = String(v);
            };
            set('hdata_00_sizeno_x',  cut);   // 재단 가로
            set('hdata_00_sizeno_y',  cut);   // 재단 세로
            set('hdata_00_sizeno_xx', work);  // 작업 가로
            set('hdata_00_sizeno_yy', work);  // 작업 세로
        }""",
        [size_mm, work_size],
    )
    time.sleep(0.5)

    # 3) 기본 옵션 재설정 (용지/색도/수량)
    setup_base_options(page)
    time.sleep(1.5)

    # 4) 가격 계산 트리거
    page.evaluate(
        "() => { if (typeof fnOrdSummary === 'function') fnOrdSummary(); }"
    )
    time.sleep(3)

    price = get_price(page)
    if price == 0:
        print(f"  [WARN] 가격 0원 - 스크린샷 저장")
        save_screenshot(page, f"error_irregular_{size_mm}")

    print(f"    가격: {price:,}원")
    return {"price": price}


# ─── 아이템 빌더 ────────────────────────────────────────────────────────────────

def build_item(product_name: str, paper_name: str, url: str, size_str: str, price: int) -> dict:
    return {
        "product": product_name,
        "category": "스티커",
        "paper_name": paper_name,
        "coating": "유광코팅",
        "print_mode": "단면칼라",
        "size": size_str,
        "qty": 1000,
        "price": price,
        "price_vat_included": True,
        "url": url,
        "url_ok": price > 0,
        "options": {
            "shape": "원형",
            "ea_per_sheet": 1,
        },
    }


# ─── DOM 덤프 (디버깅) ──────────────────────────────────────────────────────────

def dump_dom(page: Page) -> None:
    """select / input 요소 전체를 output/wowpress_sticker_dom.json에 저장"""
    dom_data = page.evaluate(
        """() => {
            const result = { selects: [], inputs: [] };
            document.querySelectorAll('select').forEach(el => {
                result.selects.push({
                    id: el.id, name: el.name,
                    onchange: el.getAttribute('onchange'),
                    display: window.getComputedStyle(el).display,
                    value: el.value,
                    options: Array.from(el.options).map(o => ({
                        value: o.value, text: o.text.trim()
                    }))
                });
            });
            document.querySelectorAll('input').forEach(el => {
                result.inputs.push({
                    id: el.id, name: el.name, type: el.type,
                    value: el.value,
                    onchange: el.getAttribute('onchange'),
                    display: window.getComputedStyle(el).display
                });
            });
            return result;
        }"""
    )
    path = os.path.join(OUTPUT_DIR, "wowpress_sticker_dom.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dom_data, f, ensure_ascii=False, indent=2)
    print(f"[DOM dump] {path}")



_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sticker_targets.json"


def _load_targets() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg.get("wowpress", [])


# ─── 메인 크롤러 ────────────────────────────────────────────────────────────────

def crawl_all(headless: bool = True) -> list[dict]:
    """
    sticker_targets.json wowpress 섹션의 전체 제품 × 용지 × 사이즈 크롤링.
    여러 제품 페이지(ProdNo)를 순회.
    """
    ensure_output_dir()
    items: list[dict] = []
    targets = _load_targets()

    if not targets:
        print("크롤 타겟 없음")
        return items

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        for ti, t in enumerate(targets):
            product_url = t["url"]
            product_name = t["product_name"]
            print(f"\n[{ti+1}/{len(targets)}] {product_name}: {product_url}")

            page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            if ti == 0:
                dump_dom(page)

            for paper in t.get("papers", [{}]):
                paper_name = paper.get("name", product_name)
                print(f"\n  용지: {paper_name}")

                # 용지 선택 (paperno3에서 텍스트 매칭)
                page.evaluate(r"""([paperText]) => {
                    const sel = document.getElementById('spdata_00_paperno3');
                    if (!sel) return;
                    const opt = [...sel.options].find(o => o.textContent.includes(paperText));
                    if (opt) {
                        sel.value = opt.value;
                        const oc = sel.getAttribute('onchange');
                        if (oc) { try { (new Function('event', oc)).call(sel, null); } catch(e) {} }
                    }
                }""", [paper_name])
                time.sleep(1.5)

                # paperno4 첫 번째 옵션 자동 선택
                page.evaluate(r"""() => {
                    const sel = document.getElementById('spdata_00_paperno4');
                    if (sel && sel.options.length > 0) {
                        sel.value = sel.options[0].value;
                        const oc = sel.getAttribute('onchange');
                        if (oc) { try { (new Function('event', oc)).call(sel, null); } catch(e) {} }
                    }
                }""")
                time.sleep(1.0)

                # 색도 + 수량 + 건수
                set_select_and_trigger(page, "#pdata_00_colorno", "302")
                time.sleep(0.5)
                set_select_and_trigger(page, "#spdata_00_ordqty", "1000")
                time.sleep(0.5)
                set_select_and_trigger(page, "#pdata_00_ordcnt", "1")
                time.sleep(1.0)

                # 프리셋 사이즈
                for size_info in t.get("preset_sizes", []):
                    size_name = size_info["name"]
                    label = size_info["label"]
                    size_value = PRESET_SIZES.get(int(size_name.split("x")[0]))

                    if size_value:
                        item = crawl_preset_size(page, int(size_name.split("x")[0]))
                        item = build_item(product_name, paper_name, product_url, size_name, item["price"] if isinstance(item, dict) else 0)
                    else:
                        item = build_item(product_name, paper_name, product_url, size_name, 0)

                    if item["price"] > 0:
                        items.append(item)

                # 비규격 사이즈
                for size_info in t.get("custom_sizes", []):
                    size_mm = size_info["mm"]
                    size_name = size_info["name"]
                    item_raw = crawl_irregular_size(page, size_mm)
                    item = build_item(product_name, paper_name, product_url, size_name, item_raw["price"] if isinstance(item_raw, dict) else 0)
                    if item["price"] > 0:
                        items.append(item)

        browser.close()

    return items


# ─── 저장 ───────────────────────────────────────────────────────────────────────

def save(items: list[dict]) -> None:
    """output/wowpress_sticker_raw_now.json 에 저장"""
    ensure_output_dir()
    now    = datetime.now().strftime("%Y-%m-%d %H:%M")
    output = {
        "company":    COMPANY,
        "crawled_at": now,
        "items":      items,
    }
    path = os.path.join(OUTPUT_DIR, "wowpress_sticker_raw_now.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"[저장 완료] {path}")
    print(f"  총 {len(items)}개 아이템  |  crawled_at: {now}")
    print(f"{'─'*50}")
    for it in items:
        mark = "✓" if it["url_ok"] else "✗"
        print(f"  {mark}  {it['size']:8s}  {it['price']:>8,}원")
    print(f"{'='*50}")


# ─── 진입점 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    # --show 옵션: 브라우저 창 표시 (디버깅용)
    headless = "--show" not in sys.argv
    print(f"=== WowpressStickerCrawler 시작 (headless={headless}) ===\n")
    items = crawl_all(headless=headless)
    save(items)