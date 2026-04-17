"""
경쟁사 가격 모니터링 대시보드.
- 스케줄러가 생성한 normalize_now.json을 그대로 읽어 표시.
- 값 변환 로직 없음 (정규화는 스케줄러가 담당).
- 카테고리별 (card, sticker) 가격 비교 + 변동 감지.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

CONFIG_DIR = os.path.join(BASE_DIR, "config")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

RULE_PATHS = {
    "card": os.path.join(CONFIG_DIR, "card_mapping_rule.json"),
    "sticker": os.path.join(CONFIG_DIR, "sticker_mapping_rule.json"),
}

CATEGORIES = [
    {"id": "card", "name": "명함"},
    {"id": "sticker", "name": "스티커"},
]

app = Flask(__name__)


# ── 상태 ──
crawl_status = {"running": False, "current": "", "elapsed_sec": 0, "errors": [], "category": ""}
status_lock = threading.Lock()
_crawl_start_time = None


def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def update_status(**kwargs):
    with status_lock:
        crawl_status.update(kwargs)


# ── 사이트 목록 ──
def get_active_sites(category: str = "card") -> list[dict]:
    rule_path = RULE_PATHS.get(category, RULE_PATHS["card"])
    rule = load_json(rule_path) or {}
    out = []
    for sid, conf in rule.get("sites", {}).items():
        if conf.get("dashboard_excluded"):
            continue
        out.append({
            "id": sid,
            "name": conf.get("name", sid),
            "base_url": conf.get("base_url", ""),
            "ownership": conf.get("ownership", "competitor"),
            "vat_adjusted": conf.get("vat_included", True) is False,
        })
    return out


def _side(print_mode: str) -> str | None:
    if print_mode.startswith("양면"):
        return "양면"
    if print_mode.startswith("단면"):
        return "단면"
    return None


# ── 명함 grid 표시 순서 ──
CARD_GRID_ORDER = [
    ("스노우화이트 250g", "비코팅", False),
    ("스노우화이트 250g", "유광코팅", False),
    ("스노우화이트 250g", "무광코팅", False),
    ("스노우화이트 216g", "비코팅", False),
    ("스노우화이트 216g", "유광코팅", False),
    ("스노우화이트 216g", "무광코팅", False),
    ("스노우화이트 300g", "비코팅", False),
    ("스노우화이트 300g", "유광코팅", False),
    ("스노우화이트 300g", "무광코팅", False),
    ("스노우화이트 400g", "비코팅", False),
    ("스노우화이트 400g", "유광코팅", False),
    ("스노우화이트 400g", "무광코팅", False),
    ("누브 210g", "비코팅", False),
    ("반누보화이트 250g", "비코팅", False),
    ("아르떼 울트라화이트 310g", "비코팅", False),
    ("누브 350g", "비코팅", False),
    ("휘라레 216g", "비코팅", False),
    ("스노우화이트 300g", "비코팅", True),
]


# ── 공통: 사이트별 데이터 로드 ──
def _load_site_data(category: str, site_ids: list[str]):
    items_by_site = {}
    raw_items_by_site = {}
    latest_crawled = None
    for sid in site_ids:
        data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_{category}_normalize_now.json"))
        items_by_site[sid] = data.get("items", []) if data else []
        if data and data.get("crawled_at"):
            if latest_crawled is None or data["crawled_at"] > latest_crawled:
                latest_crawled = data["crawled_at"]
        raw_data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_{category}_raw_now.json"))
        raw_items_by_site[sid] = raw_data.get("items", []) if raw_data else []
    return items_by_site, raw_items_by_site, latest_crawled


def _find_raw_paper_name(matching, items_by_site_sid, raw_items_by_site_sid):
    """normalize item index → raw item의 같은 index에서 원래 용지명."""
    if not matching:
        return ""
    for norm_idx, nit in enumerate(items_by_site_sid):
        if nit is matching[0]:
            if norm_idx < len(raw_items_by_site_sid):
                return raw_items_by_site_sid[norm_idx].get("paper_name", "")
            break
    return ""


# ── 명함 grid ──
def _build_card_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled):
    seen_keys = []
    for key in CARD_GRID_ORDER:
        paper_name, coating, partial = key
        for sid in site_ids:
            for it in items_by_site[sid]:
                if (it.get("paper_name") == paper_name
                    and it.get("coating") == coating
                    and bool(it.get("options", {}).get("partial_coating")) == partial):
                    seen_keys.append(key)
                    break
            else:
                continue
            break

    qtys = [100, 200, 500, 1000]
    sides = ["단면", "양면"]

    papers = []
    for paper_name, coating, partial in seen_keys:
        prefix = "[부분코팅] " if partial else ""
        label = f"{prefix}{paper_name} {coating}".strip()
        entry = {"label": label, "paper_name": paper_name, "coating": coating, "partial_coating": partial, "sites": {}}
        for site in sites:
            sid = site["id"]
            matching = [
                it for it in items_by_site[sid]
                if it.get("paper_name") == paper_name
                and it.get("coating") == coating
                and bool(it.get("options", {}).get("partial_coating")) == partial
            ]
            if not matching:
                entry["sites"][sid] = None
                continue
            url = matching[0].get("url") or site["base_url"]
            url_ok = matching[0].get("url_ok", True)
            prices = {s: {str(q): None for q in qtys} for s in sides}
            products_seen = []
            for it in matching:
                p = it.get("product", "")
                if p and p not in products_seen:
                    products_seen.append(p)
                side = _side(it.get("print_mode", ""))
                if side not in prices:
                    continue
                q = it.get("qty")
                if q in qtys:
                    prices[side][str(q)] = it.get("price")
            raw_paper_name = _find_raw_paper_name(matching, items_by_site[sid], raw_items_by_site.get(sid, []))
            entry["sites"][sid] = {
                "product": " + ".join(products_seen),
                "raw_paper_name": raw_paper_name,
                "url": url, "url_ok": url_ok, "prices": prices,
            }
        papers.append(entry)

    return {"type": "card", "sites": sites, "sides": sides, "qtys": qtys, "papers": papers, "updated_at": latest_crawled}


# ── 스티커 grid ──
STICKER_SIZES = ["45x45", "55x55", "65x65", "75x75", "85x85", "95x95"]

def _build_sticker_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled):
    # 용지별 그룹: (paper_name, coating) 키 수집
    seen_keys = []
    seen_set = set()
    for sid in site_ids:
        for it in items_by_site[sid]:
            key = (it.get("paper_name", ""), it.get("coating", ""))
            if key not in seen_set:
                seen_set.add(key)
                seen_keys.append(key)

    papers = []
    for paper_name, coating in seen_keys:
        label = f"{paper_name} {coating}".strip()
        entry = {"label": label, "paper_name": paper_name, "coating": coating, "sites": {}}
        for site in sites:
            sid = site["id"]
            matching = [
                it for it in items_by_site[sid]
                if it.get("paper_name") == paper_name and it.get("coating") == coating
            ]
            if not matching:
                entry["sites"][sid] = None
                continue
            url = matching[0].get("url") or site["base_url"]
            url_ok = matching[0].get("url_ok", True)
            products_seen = []
            # 사이즈별 가격 + ea_per_sheet 보정
            prices = {}
            for it in matching:
                p = it.get("product", "")
                if p and p not in products_seen:
                    products_seen.append(p)
                size = it.get("size", "")
                ea = it.get("options", {}).get("ea_per_sheet", 1)
                price = it.get("price")
                if price is not None and ea > 1:
                    price = price // ea
                prices[size] = {"price": price, "ea": ea}
            raw_paper_name = _find_raw_paper_name(matching, items_by_site[sid], raw_items_by_site.get(sid, []))
            entry["sites"][sid] = {
                "product": " + ".join(products_seen),
                "raw_paper_name": raw_paper_name,
                "url": url, "url_ok": url_ok, "prices": prices,
            }
        papers.append(entry)

    return {"type": "sticker", "sites": sites, "sizes": STICKER_SIZES, "papers": papers, "updated_at": latest_crawled}


# ── grid API ──
@app.route("/api/data/grid")
def api_grid():
    category = request.args.get("category", "card")
    sites = get_active_sites(category)
    site_ids = [s["id"] for s in sites]
    items_by_site, raw_items_by_site, latest_crawled = _load_site_data(category, site_ids)

    if category == "sticker":
        return jsonify(_build_sticker_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled))
    return jsonify(_build_card_grid(sites, site_ids, items_by_site, raw_items_by_site, latest_crawled))


# ── 변동 감지 API ──
def _match_key(item: dict) -> tuple:
    return (
        item.get("paper_name", ""),
        item.get("coating", ""),
        item.get("print_mode", ""),
        item.get("size", ""),
        item.get("qty", 0),
        bool(item.get("options", {}).get("partial_coating")),
    )


@app.route("/api/data/changes")
def api_changes():
    category = request.args.get("category", "card")
    sites = get_active_sites(category)
    rule_path = RULE_PATHS.get(category, RULE_PATHS["card"])
    rule = load_json(rule_path) or {}
    site_names = {sid: conf.get("name", sid) for sid, conf in rule.get("sites", {}).items()}

    changes = []
    past_time = now_time = None
    for site in sites:
        sid = site["id"]
        past_data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_{category}_normalize_past.json"))
        now_data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_{category}_normalize_now.json"))
        if not past_data or not now_data:
            continue
        if now_data.get("crawled_at") and (now_time is None or now_data["crawled_at"] > now_time):
            now_time = now_data["crawled_at"]
        if past_data.get("crawled_at") and (past_time is None or past_data["crawled_at"] > past_time):
            past_time = past_data["crawled_at"]
        past = {_match_key(it): it for it in past_data.get("items", [])}
        now = {_match_key(it): it for it in now_data.get("items", [])}
        for k, now_it in now.items():
            past_it = past.get(k)
            if not past_it:
                continue
            pp, np_ = past_it.get("price"), now_it.get("price")
            if not (pp and np_) or pp == np_:
                continue
            changes.append({
                "company": sid, "company_name": site_names.get(sid, sid),
                "paper_name": k[0], "coating": k[1], "print_mode": k[2],
                "size": k[3], "qty": k[4], "partial_coating": k[5],
                "past_price": pp, "now_price": np_, "diff": np_ - pp,
                "pct": round((np_ - pp) / pp * 100, 2),
                "direction": "up" if np_ > pp else "down",
            })
    changes.sort(key=lambda c: abs(c["pct"]), reverse=True)
    return jsonify({"past_time": past_time, "now_time": now_time, "total": len(changes), "changes": changes})


# ── 크롤 트리거 ──
def run_crawl(category: str):
    global _crawl_start_time
    _crawl_start_time = time.time()
    update_status(running=True, current=f"{category} 파이프라인 실행 중...", elapsed_sec=0, errors=[], category=category)
    try:
        from scheduler import run_category
        run_category(category)
    except Exception as e:
        with status_lock:
            crawl_status["errors"].append(str(e))
    total = int(time.time() - _crawl_start_time)
    update_status(running=False, current="완료", elapsed_sec=total)


@app.route("/")
def index():
    return render_template("index.html", categories=CATEGORIES)


@app.route("/api/categories")
def api_categories():
    return jsonify(CATEGORIES)


@app.route("/api/status")
def api_status():
    with status_lock:
        return jsonify(crawl_status)


@app.route("/api/start", methods=["POST"])
def api_start():
    if crawl_status["running"]:
        return jsonify({"error": "이미 실행 중"}), 409
    data = request.get_json(silent=True) or {}
    category = data.get("category", "card")
    threading.Thread(target=run_crawl, args=(category,), daemon=True).start()
    return jsonify({"status": "started", "category": category})


@app.route("/stream")
def stream():
    def generate():
        while True:
            with status_lock:
                running = crawl_status["running"]
                current = crawl_status["current"]
                elapsed = crawl_status.get("elapsed_sec", 0)
                cat = crawl_status.get("category", "")
            if running and _crawl_start_time:
                elapsed = int(time.time() - _crawl_start_time)
            yield f"data: {json.dumps({'running': running, 'current': current, 'elapsed_sec': elapsed, 'category': cat}, ensure_ascii=False)}\n\n"
            if not running and current == "완료":
                yield f"data: {json.dumps({'done': True, 'elapsed_sec': elapsed, 'category': cat})}\n\n"
                break
            time.sleep(0.5)
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    app.run(host="0.0.0.0", port=5001, threaded=True)
