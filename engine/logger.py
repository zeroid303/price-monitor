"""JSONL + 콘솔 이중 출력 로거.

이벤트 키: run.start / fetch.start / fetch.ok / fetch.fail /
          extract.item / extract.warn / normalize.ok / validate.fail /
          store.ok / store.rotate / run.done / run.fail
"""
import json
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

_CONSOLE_SUMMARY_KEYS = (
    "site", "category", "product", "paper_name", "coating",
    "print_mode", "size", "qty", "price", "count",
    "raw_count", "norm_count", "target_count", "item_id", "error", "file",
)


class RunLogger:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.run_dir = LOGS_DIR / run_id
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = LOGS_DIR / f"{run_id}.jsonl"
        self._jsonl = open(self.jsonl_path, "a", encoding="utf-8")

        # Windows 기본 콘솔이 cp949라 한글이 깨짐 → utf-8 재설정
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

        self.console = logging.getLogger(f"run.{run_id[:8]}")
        if not self.console.handlers:
            h = logging.StreamHandler(sys.stdout)
            h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            self.console.addHandler(h)
            self.console.setLevel(logging.INFO)
            self.console.propagate = False

    def event(self, kind: str, level: str = "info", **fields) -> None:
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "kind": kind,
            **fields,
        }
        self._jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._jsonl.flush()

        summary_parts = [f"{k}={fields[k]}" for k in _CONSOLE_SUMMARY_KEYS if k in fields]
        msg = f"[{kind}] " + " ".join(summary_parts) if summary_parts else f"[{kind}]"
        getattr(self.console, level if level in ("info", "warning", "error") else "info")(msg)

    def save_artifact(
        self,
        item_id: str,
        page=None,
        exc: Optional[BaseException] = None,
        html: Optional[str] = None,
    ) -> None:
        """에러 artifact 한 세트(.png/.html/.json) 저장. 실패해도 run 중단 X."""
        base = self.run_dir / f"error_{item_id}"
        if page is not None:
            try:
                page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
            except Exception:
                pass
            try:
                if html is None:
                    html = page.content()
            except Exception:
                pass
        if html is not None:
            try:
                base.with_suffix(".html").write_text(html, encoding="utf-8")
            except Exception:
                pass
        if exc is not None:
            meta = {
                "item_id": item_id,
                "exception_type": type(exc).__name__,
                "exception_msg": str(exc),
                "traceback": traceback.format_exc(),
            }
            try:
                base.with_suffix(".json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

    def close(self) -> None:
        try:
            self._jsonl.close()
        except Exception:
            pass
