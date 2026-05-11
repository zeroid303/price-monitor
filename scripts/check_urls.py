"""URL 생존 체크 — 각 카테고리 normalize_now.json 의 items[].url 을 HEAD 요청으로
확인하고 url_ok 필드를 갱신.

호출:
  from scripts.check_urls import run
  run(["envelope", "card_offset"])

또는 CLI:
  python -m scripts.check_urls envelope card_offset

URL unique 단위로 한 번씩만 체크 (같은 사이트의 여러 item 이 같은 URL 공유).
HEAD 가 405/403 등으로 거부되면 GET(stream=True, range 0-0) 으로 fallback.
2xx/3xx → ok=True, 4xx/5xx/timeout/connection error → ok=False.

결과:
  - 각 normalize_now.json 의 items[].url_ok 를 in-place 업데이트
  - logger 에 사이트별 (ok/fail) 카운트 + fail URL 목록 출력
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

logger = logging.getLogger("check_urls")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
TIMEOUT = 10.0


def _request_head_or_get(url: str, *, verify: bool):
    """HEAD 시도 후 405/403/400 이면 GET (range 0-0) fallback."""
    headers = {"User-Agent": UA, "Accept": "*/*"}
    r = requests.head(url, timeout=TIMEOUT, allow_redirects=True,
                      headers=headers, verify=verify)
    if r.status_code in (405, 403, 400):
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=True,
                         headers={**headers, "Range": "bytes=0-0"},
                         stream=True, verify=verify)
        r.close()
    return r


def _is_alive(url: str) -> tuple[bool, str]:
    """URL HEAD → ok=True/False + 사유 문자열 반환.

    HEAD 가 405/403 으로 거부되면 GET (stream + range 0-0) fallback.
    SSLError 가 발생하면 verify=False 로 1회 retry (한국 쇼핑몰 사이트가
    인증서 chain 불완전한 경우 — Playwright 는 동작하지만 requests 기본
    SSL context 가 거절하는 케이스).
    """
    try:
        r = _request_head_or_get(url, verify=True)
        if 200 <= r.status_code < 400:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except requests.exceptions.SSLError:
        # SSL handshake 실패 — (a) verify=False 재시도, (b) http:// 다운그레이드 재시도.
        # swadpia 같이 cipher 협상 거부하는 서버는 (a) 도 실패하지만 http:// 로는 동작.
        # URL 생존 체크 용도라 데이터 송수신 보안 무관.
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            r = _request_head_or_get(url, verify=False)
            if 200 <= r.status_code < 400:
                return True, f"HTTP {r.status_code} (SSL bypassed)"
            return False, f"HTTP {r.status_code} (SSL bypassed)"
        except requests.exceptions.SSLError:
            # http:// 다운그레이드
            if url.startswith("https://"):
                http_url = "http://" + url[len("https://"):]
                try:
                    r = _request_head_or_get(http_url, verify=False)
                    if 200 <= r.status_code < 400:
                        return True, f"HTTP {r.status_code} (downgraded to http)"
                    return False, f"HTTP {r.status_code} (downgraded to http)"
                except Exception as e2:
                    return False, f"SSL fail + http downgrade error: {type(e2).__name__}"
            return False, "SSL handshake failure"
        except Exception as e:
            return False, f"SSL fail + retry error: {type(e).__name__}"
    except requests.exceptions.Timeout:
        return False, "timeout"
    except requests.exceptions.ConnectionError as e:
        return False, f"conn error: {type(e).__name__}"
    except Exception as e:
        return False, f"error: {type(e).__name__}"


def _check_normalize_file(path: Path) -> dict:
    """단일 normalize_now.json 의 unique URL 체크 + items[].url_ok 갱신.

    반환: {"total_items": N, "unique_urls": M, "ok": K, "fail": L, "fail_urls": [...]}
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if not items:
        return {"total_items": 0, "unique_urls": 0, "ok": 0, "fail": 0, "fail_urls": []}

    unique_urls = {it.get("url") for it in items if it.get("url")}
    unique_urls.discard(None)

    url_status: dict[str, tuple[bool, str]] = {}
    for url in sorted(unique_urls):
        ok, reason = _is_alive(url)
        url_status[url] = (ok, reason)
        if not ok:
            logger.warning(f"    ✗ {url} — {reason}")

    # items[].url_ok 갱신
    for it in items:
        u = it.get("url")
        if u and u in url_status:
            it["url_ok"] = url_status[u][0]

    # 저장
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    fail_urls = [u for u, (ok, _) in url_status.items() if not ok]
    return {
        "total_items": len(items),
        "unique_urls": len(unique_urls),
        "ok": len(unique_urls) - len(fail_urls),
        "fail": len(fail_urls),
        "fail_urls": fail_urls,
    }


def run(categories: list[str]) -> dict:
    """각 카테고리의 모든 사이트 normalize_now.json 에 대해 URL 체크 + url_ok 갱신.

    반환: {category: {site: stats}} 요약.
    """
    summary: dict = {}
    for cat in categories:
        cat_summary = {}
        files = sorted(OUTPUT_DIR.glob(f"*_{cat}_normalize_now.json"))
        if not files:
            logger.warning(f"  [{cat}] normalize_now.json 없음")
            continue
        for f in files:
            site = f.name.split(f"_{cat}_")[0]
            logger.info(f"  [{cat}/{site}] {f.name}")
            stats = _check_normalize_file(f)
            cat_summary[site] = stats
            logger.info(f"    items={stats['total_items']} unique_urls={stats['unique_urls']} "
                        f"ok={stats['ok']} fail={stats['fail']}")
        summary[cat] = cat_summary
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="URL 생존 체크 — normalize_now.json 의 url_ok 갱신")
    p.add_argument("categories", nargs="+",
                   help="확인할 카테고리 (예: envelope card_offset card_digital flyer)")
    args = p.parse_args()
    run(args.categories)


if __name__ == "__main__":
    main()
