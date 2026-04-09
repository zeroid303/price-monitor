"""크롤러 베이스 클래스"""
import json
import csv
import os
import logging
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

from config.settings import OUTPUT_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


@dataclass
class PriceRecord:
    """크롤링 결과 레코드"""
    crawled_at: str                    # 크롤링 일시
    competitor: str                    # 경쟁사 이름
    category: str                      # 제품 카테고리 (명함/전단/스티커/현수막/리플렛)
    spec: str                          # 제품 사양 (용지, 수량, 코팅 등)
    price: Optional[int] = None        # 가격 (세전, 원/엔)
    currency: str = "KRW"             # 통화
    delivery: str = ""                 # 납기
    source_url: str = ""               # 원본 URL
    error: str = ""                    # 에러 메시지 (크롤링 실패 시)


class BaseCrawler:
    """모든 크롤러의 베이스 클래스"""

    site_name: str = ""
    base_url: str = ""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.results: list[PriceRecord] = []

    def now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def add_result(self, **kwargs):
        record = PriceRecord(
            crawled_at=self.now(),
            competitor=self.site_name,
            **kwargs,
        )
        self.results.append(record)
        self.logger.info(f"  → {record.category} | {record.spec} | {record.price} {record.currency}")

    def add_error(self, category: str, spec: str, error: str):
        record = PriceRecord(
            crawled_at=self.now(),
            competitor=self.site_name,
            category=category,
            spec=spec,
            error=error,
        )
        self.results.append(record)
        self.logger.error(f"  ✗ {category} | {spec} | ERROR: {error}")

    def save_json(self, filename: str = None):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.__class__.__name__}_{timestamp}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)
        data = [asdict(r) for r in self.results]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.logger.info(f"JSON 저장 완료: {filepath}")
        return filepath

    def save_csv(self, filename: str = None):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.__class__.__name__}_{timestamp}.csv"
        filepath = os.path.join(OUTPUT_DIR, filename)
        if not self.results:
            self.logger.warning("저장할 결과가 없습니다.")
            return None
        fieldnames = list(asdict(self.results[0]).keys())
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self.results:
                writer.writerow(asdict(r))
        self.logger.info(f"CSV 저장 완료: {filepath}")
        return filepath

    async def crawl(self):
        """서브클래스에서 구현"""
        raise NotImplementedError

    async def run(self):
        """크롤링 실행 후 결과 저장"""
        self.logger.info(f"=== {self.site_name} 크롤링 시작 ===")
        try:
            await self.crawl()
        except Exception as e:
            self.logger.error(f"크롤링 중 치명적 오류: {e}")
            self.add_error("전체", "전체", str(e))
        self.save_json()
        self.save_csv()
        self.logger.info(f"=== {self.site_name} 크롤링 완료 ({len(self.results)}건) ===")
        return self.results
