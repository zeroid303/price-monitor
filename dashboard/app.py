"""
경쟁사 가격 모니터링 대시보드.
- 스케줄러가 생성한 normalize_now.json을 그대로 읽어 표시.
- 값 변환 로직 없음 (정규화는 스케줄러가 담당).
- 가격 업데이트 버튼은 scheduler.run_category('card')를 호출.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, stream_with_context

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

CONFIG_DIR = os.path.join(BASE_DIR, "config")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CARD_RULE_PATH = os.path.join(CONFIG_DIR, "card_mapping_rule.json")

app = Flask(__name__)


# ── 상태 ──
crawl_status = {"running": False, "current": "", "elapsed_sec": 0, "errors": []}
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


# ── 사이트 목록 (card_mapping_rule.sites, dashboard_excluded 제외) ──
def get_active_sites() -> list[dict]:
    rule = load_json(CARD_RULE_PATH) or {}
    out = []
    for sid, conf in rule.get("sites", {}).items():
        if conf.get("dashboard_excluded"):
            continue
        out.append({
            "id": sid,
            "name": conf.get("name", sid),
            "base_url": conf.get("base_url", ""),
            "ownership": conf.get("ownership", "competitor"),
        })
    return out


def _side(print_mode: str) -> str | None:
    if print_mode.startswith("양면"):
        return "양면"
    if print_mode.startswith("단면"):
        return "단면"
    return None


# ── grid 데이터 빌드 ──
@app.route("/api/data/grid")
def api_grid():
    """대시보드 grid용 데이터. normalize_now.json만 사용."""
    sites = get_active_sites()
    site_ids = [s["id"] for s in sites]

    items_by_site: dict[str, list] = {}
    latest_crawled = None
    for sid in site_ids:
        data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_card_normalize_now.json"))
        items_by_site[sid] = data.get("items", []) if data else []
        if data and data.get("crawled_at"):
            if latest_crawled is None or data["crawled_at"] > latest_crawled:
                latest_crawled = data["crawled_at"]

    # (paper_name, coating, partial) 키 수집
    seen_keys = []
    seen_set = set()
    for sid in site_ids:
        for it in items_by_site[sid]:
            partial = bool(it.get("options", {}).get("partial_coating"))
            key = (it.get("paper_name", ""), it.get("coating", ""), partial)
            if key in seen_set:
                continue
            seen_set.add(key)
            seen_keys.append(key)

    qtys = [100, 200, 500, 1000]
    sides = ["단면", "양면"]

    papers = []
    for paper_name, coating, partial in seen_keys:
        prefix = "[부분코팅] " if partial else ""
        label = f"{prefix}{paper_name} {coating}".strip()
        entry = {
            "label": label,
            "paper_name": paper_name,
            "coating": coating,
            "partial_coating": partial,
            "sites": {},
        }
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
            product = matching[0].get("product", "")
            url = matching[0].get("url") or site["base_url"]
            url_ok = matching[0].get("url_ok", True)
            prices = {s: {str(q): None for q in qtys} for s in sides}
            for it in matching:
                side = _side(it.get("print_mode", ""))
                if side not in prices:
                    continue
                q = it.get("qty")
                if q in qtys:
                    prices[side][str(q)] = it.get("price")
            entry["sites"][sid] = {
                "product": product,
                "url": url,
                "url_ok": url_ok,
                "prices": prices,
            }
        papers.append(entry)

    return jsonify({
        "sites": sites,
        "sides": sides,
        "qtys": qtys,
        "papers": papers,
        "updated_at": latest_crawled,
    })


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
    """각 사이트의 normalize_past vs normalize_now 비교해 가격 변동 즉시 계산."""
    sites = get_active_sites()
    rule = load_json(CARD_RULE_PATH) or {}
    site_names = {sid: conf.get("name", sid) for sid, conf in rule.get("sites", {}).items()}

    changes = []
    past_time = now_time = None
    for site in sites:
        sid = site["id"]
        past_data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_card_normalize_past.json"))
        now_data = load_json(os.path.join(OUTPUT_DIR, f"{sid}_card_normalize_now.json"))
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
            pp, np = past_it.get("price"), now_it.get("price")
            if not (pp and np) or pp == np:
                continue
            changes.append({
                "company": sid,
                "company_name": site_names.get(sid, sid),
                "paper_name": k[0],
                "coating": k[1],
                "print_mode": k[2],
                "size": k[3],
                "qty": k[4],
                "partial_coating": k[5],
                "past_price": pp,
                "now_price": np,
                "diff": np - pp,
                "pct": round((np - pp) / pp * 100, 2),
                "direction": "up" if np > pp else "down",
            })

    changes.sort(key=lambda c: abs(c["pct"]), reverse=True)
    return jsonify({
        "past_time": past_time,
        "now_time": now_time,
        "total": len(changes),
        "changes": changes,
    })


# ── 크롤 트리거 (scheduler.run_category 호출) ──
def run_crawl_card():
    global _crawl_start_time
    _crawl_start_time = time.time()
    update_status(running=True, current="card 파이프라인 실행 중...", elapsed_sec=0, errors=[])
    try:
        from scheduler import run_category
        run_category("card")
    except Exception as e:
        with status_lock:
            crawl_status["errors"].append(str(e))
    total = int(time.time() - _crawl_start_time)
    update_status(running=False, current="완료", elapsed_sec=total)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    with status_lock:
        return jsonify(crawl_status)


@app.route("/api/start", methods=["POST"])
def api_start():
    if crawl_status["running"]:
        return jsonify({"error": "이미 실행 중"}), 409
    threading.Thread(target=run_crawl_card, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/stream")
def stream():
    def generate():
        while True:
            with status_lock:
                running = crawl_status["running"]
                current = crawl_status["current"]
                elapsed = crawl_status.get("elapsed_sec", 0)
            if running and _crawl_start_time:
                elapsed = int(time.time() - _crawl_start_time)
            yield f"data: {json.dumps({'running': running, 'current': current, 'elapsed_sec': elapsed}, ensure_ascii=False)}\n\n"
            if not running and current == "완료":
                yield f"data: {json.dumps({'done': True, 'elapsed_sec': elapsed})}\n\n"
                break
            time.sleep(0.5)
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    app.run(host="0.0.0.0", port=5001, threaded=True)
