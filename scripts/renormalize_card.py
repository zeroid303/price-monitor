"""raw_now 파일들에 새 schema 적용해서 normalize_now 재생성.

raw 재크롤 X — 기존 raw_now 만 정규화 다시.
"""
import json
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from common import normalize


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    output_dir = os.path.join(ROOT, "output")

    for category in ("card_offset", "card_digital"):
        schema_path = os.path.join(ROOT, f"config/schemas/{category}.yaml")
        if not os.path.exists(schema_path):
            print(f"  ⚠ schema 없음: {schema_path}")
            continue
        schema = yaml.safe_load(open(schema_path, encoding="utf-8"))
        norm_rule = schema.get("_normalization", {})

        for fn in sorted(os.listdir(output_dir)):
            if not fn.endswith("_raw_now.json"): continue
            if f"_{category}_" not in fn: continue
            raw_path = os.path.join(output_dir, fn)
            norm_path = raw_path.replace("_raw_now.json", "_normalize_now.json")

            d = json.load(open(raw_path, encoding="utf-8"))
            new_items = [normalize.apply(it, norm_rule) for it in d.get("items", [])]
            payload = {**{k: v for k, v in d.items() if k != "items"}, "items": new_items}
            with open(norm_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"  {fn} → {os.path.basename(norm_path)} ({len(new_items)} items)")


if __name__ == "__main__":
    main()
