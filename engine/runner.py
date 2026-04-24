"""오케스트레이터: fetch_and_extract → normalize → store.

사용:
    python -m engine.runner <site> <category>
    예) python -m engine.runner dtpia card
"""
import argparse
import hashlib
import importlib
import json
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from common import normalize
from . import store
from .adapter import SiteAdapter
from .context import RawItem, RunContext
from .logger import RunLogger

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

# 카테고리 → schema 공유 맵. card_offset / card_digital 은 동일 schemas/card.yaml 을 공유.
# targets 는 카테고리별로 별도 파일.
_SCHEMA_ALIAS = {
    "card_offset": "card",
    "card_digital": "card",
}

# 이관 과도기용 legacy JSON 경로. 신규 yaml 없으면 여기로 폴백.
_LEGACY_TARGETS = {
    "card": CONFIG_DIR / "card_targets.json",
    "envelope": CONFIG_DIR / "envelope_targets.json",
    "sticker": CONFIG_DIR / "sticker_targets.json",
}
_LEGACY_SCHEMA = {
    "card": CONFIG_DIR / "card_mapping_rule.json",
    "envelope": CONFIG_DIR / "envelope_mapping_rule.json",
    "sticker": CONFIG_DIR / "sticker_mapping_rule.json",
    "flyer": CONFIG_DIR / "flyer_mapping_rule.json",
}


def _read_structured(path: Path) -> dict:
    """.yaml/.yml → yaml.safe_load, .json → json.loads."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(text) or {}
    return json.loads(text)


def _load_site_config(site: str) -> dict:
    path = CONFIG_DIR / "sites" / f"{site}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"site config not found: {path}")
    return _read_structured(path)


def _load_schema(category: str) -> dict:
    """카테고리 스키마 로드. _SCHEMA_ALIAS 로 카테고리 공유 가능."""
    schema_key = _SCHEMA_ALIAS.get(category, category)
    new_yaml = CONFIG_DIR / "schemas" / f"{schema_key}.yaml"
    new_json = CONFIG_DIR / "schemas" / f"{schema_key}.json"
    if new_yaml.exists():
        return _read_structured(new_yaml)
    if new_json.exists():
        return _read_structured(new_json)
    legacy = _LEGACY_SCHEMA.get(schema_key)
    if legacy and legacy.exists():
        return _read_structured(legacy)
    raise FileNotFoundError(f"schema not found for category: {category}")


def _load_targets(site: str, category: str):
    """카테고리 targets 로드. 사이트 섹션만 반환.

    반환 타입은 사이트마다 다를 수 있음:
    - 타사: list[dict] (기존 구조)
    - 프린트시티: dict (items/filters 포함)
    어댑터가 자기에게 맞는 형태를 기대.
    """
    new_yaml = CONFIG_DIR / "targets" / f"{category}.yaml"
    new_json = CONFIG_DIR / "targets" / f"{category}.json"
    legacy = _LEGACY_TARGETS.get(category)
    for p in (new_yaml, new_json, legacy):
        if p and p.exists():
            data = _read_structured(p)
            return data.get(site, [])
    raise FileNotFoundError(f"targets not found for category: {category}")


def _load_adapter(site: str, category: str) -> SiteAdapter:
    mod = importlib.import_module(f"adapters.{site}_{category}")
    if hasattr(mod, "adapter"):
        return mod.adapter
    return mod.Adapter()


def make_item_id(site: str, category: str, raw: RawItem) -> str:
    key = "|".join(
        str(x) for x in (
            site, category, raw.product, raw.paper_name,
            raw.coating, raw.print_mode, raw.size, raw.qty,
        )
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def run(site: str, category: str) -> dict:
    run_id = uuid.uuid4().hex[:12]
    logger = RunLogger(run_id)
    try:
        site_config = _load_site_config(site)
        schema = _load_schema(category)
        targets = _load_targets(site, category)
        ctx = RunContext(
            run_id=run_id, site=site, category=category,
            site_config=site_config, schema=schema, targets=targets,
            log=logger,
        )
        logger.event(
            "run.start", site=site, category=category, target_count=len(targets)
        )
        adapter = _load_adapter(site, category)

        norm_rule = schema.get("_normalization", {})
        raw_dicts: list[dict] = []
        norm_dicts: list[dict] = []

        for raw in adapter.fetch_and_extract(ctx):
            raw.item_id = make_item_id(site, category, raw)
            logger.event(
                "extract.item",
                item_id=raw.item_id,
                product=raw.product,
                paper_name=raw.paper_name,
                coating=raw.coating,
                print_mode=raw.print_mode,
                size=raw.size,
                qty=raw.qty,
                price=raw.price,
            )
            raw_d = raw.to_dict()
            raw_dicts.append(raw_d)
            norm_d = normalize.apply(raw_d, norm_rule)
            norm_dicts.append(norm_d)
            logger.event("normalize.ok", item_id=raw.item_id)

        crawled_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        store.write(ctx, raw_dicts, norm_dicts, crawled_at)
        logger.event("run.done", count=len(raw_dicts))
        return {"run_id": run_id, "count": len(raw_dicts)}
    except Exception as e:
        logger.event("run.fail", level="error", error=str(e))
        raise
    finally:
        logger.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Price monitor run — single site/category")
    p.add_argument("site", help="예: dtpia, wowpress, swadpia, printcity")
    p.add_argument("category", help="예: card, envelope, sticker, flyer")
    args = p.parse_args()
    run(args.site, args.category)


if __name__ == "__main__":
    main()
