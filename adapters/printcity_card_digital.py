"""프린트시티 디지털 명함 어댑터.

데이터 소스: config/targets/card_digital.yaml 의 printcity 섹션.
offset 어댑터와 로직 동일 — _yield_from_targets 공용 헬퍼 사용.
"""
from typing import Iterator

from adapters.printcity_card_offset import _yield_from_targets
from engine.adapter import SiteAdapter
from engine.context import RawItem, RunContext


class Adapter(SiteAdapter):
    site = "printcity"
    category = "card_digital"

    def fetch_and_extract(self, ctx: RunContext) -> Iterator[RawItem]:
        yield from _yield_from_targets(ctx, expected_products=("디지털명함",))
