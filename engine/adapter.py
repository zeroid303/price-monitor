"""SiteAdapter 추상 클래스.

사이트×카테고리 1쌍당 1 구현체.
엔진(runner)이 주입한 RunContext만 사용해 DOM 실측값을 RawItem으로 yield.
정규화/저장/로테이션은 엔진이 담당 — 어댑터는 오직 fetch + extract.
"""
from abc import ABC, abstractmethod
from typing import Iterator

from .context import RawItem, RunContext


class SiteAdapter(ABC):
    site: str = ""
    category: str = ""

    @abstractmethod
    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        """타겟 순회하며 DOM 실측 raw item 하나씩 yield.

        실패 시: ctx.log.event("fetch.fail"/"extract.warn", ...) 기록 후
                해당 항목만 skip 또는 RawItem(price=None, url_ok=False) yield.
        """
        raise NotImplementedError
