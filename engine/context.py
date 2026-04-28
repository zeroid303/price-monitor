"""실행 단위 상태(RunContext)와 DOM 실측 레코드(RawItem)."""
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .logger import RunLogger


@dataclass
class RawItem:
    """DOM 실측 한 건. 합성·조작 금지 — 사이트 표시값(셀렉터 selected text /
    페이지 고정 표시값) 만 기록. 정규화·매칭 결정은 별도 단계.

    원칙:
      - paper_name/paper_weight_text/coating/print_mode/size/qty/price = DOM 실측
      - product/category = target 라벨 (사이트 메뉴 카테고리 식별용)
      - price_vat_included = 사이트 정책 (메타)
      - url/url_ok = 브라우저 정보
      - match_as / config_* 류는 raw 에 들어가면 안 됨 (target/schemas 에서 처리)
    """
    product: str = ""
    category: str = ""
    paper_name: Optional[str] = None         # .mtrl-name div 또는 select text 그대로
    paper_weight_text: Optional[str] = None  # weight select text 그대로 (예: "230g")
    coating: Optional[str] = None
    print_mode: Optional[str] = None
    size: Optional[str] = None
    qty: Optional[int] = None
    price: Optional[int] = None
    price_vat_included: Optional[bool] = None
    url: str = ""
    url_ok: bool = True
    options: dict = field(default_factory=dict)   # 어댑터별 메타 (prod 환경에선 비어 있어야)
    item_id: str = ""

    def to_dict(self, include_item_id: bool = False) -> dict:
        d = asdict(self)
        if not include_item_id:
            d.pop("item_id", None)
        if d.get("paper_weight_text") is None:
            d.pop("paper_weight_text", None)
        return d


@dataclass
class RunContext:
    run_id: str
    site: str
    category: str
    site_config: dict
    schema: dict
    targets: list
    log: "RunLogger"
    browser: Any = None  # playwright BrowserContext 등 (어댑터가 필요 시 세팅)
    extras: dict = field(default_factory=dict)
