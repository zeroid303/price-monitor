"""output/{site}_{category}_{raw|normalize}_{now|past}.json rotation + 쓰기.

포맷(기존과 동일):
    { "company": <site>, "crawled_at": "YYYY-MM-DD HH:MM", "items": [...] }
"""
import json
from pathlib import Path

from .context import RunContext

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def _rotate(now: Path, past: Path, ctx: RunContext) -> None:
    if now.exists():
        if past.exists():
            past.unlink()
        now.rename(past)
        ctx.log.event("store.rotate", file=now.name, to=past.name)


def write(
    ctx: RunContext,
    raw_items: list[dict],
    norm_items: list[dict],
    crawled_at: str,
) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_now = OUTPUT_DIR / f"{ctx.site}_{ctx.category}_raw_now.json"
    raw_past = OUTPUT_DIR / f"{ctx.site}_{ctx.category}_raw_past.json"
    norm_now = OUTPUT_DIR / f"{ctx.site}_{ctx.category}_normalize_now.json"
    norm_past = OUTPUT_DIR / f"{ctx.site}_{ctx.category}_normalize_past.json"

    _rotate(raw_now, raw_past, ctx)
    _rotate(norm_now, norm_past, ctx)

    payload_raw = {"company": ctx.site, "crawled_at": crawled_at, "items": raw_items}
    payload_norm = {"company": ctx.site, "crawled_at": crawled_at, "items": norm_items}

    raw_now.write_text(
        json.dumps(payload_raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    norm_now.write_text(
        json.dumps(payload_norm, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.log.event(
        "store.ok",
        raw_count=len(raw_items),
        norm_count=len(norm_items),
        file=raw_now.name,
    )
    return raw_now, norm_now
