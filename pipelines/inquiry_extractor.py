"""
質疑抽出器

信頼度の低い項目（confidence < 閾値）を自動的に質疑文に変換し、
質疑書ドラフトを生成します。
"""

from typing import List, Dict, Any
from dataclasses import dataclass
from pipelines.schemas import EstimateItem, FMTDocument
from loguru import logger


@dataclass
class Inquiry:
    """質疑項目"""
    item_name: str
    discipline: str
    question: str
    reason: str
    confidence: float
    source_reference: str


class InquiryExtractor:
    """質疑抽出器"""

    def __init__(self, confidence_threshold: float = 0.8):
        """
        Args:
            confidence_threshold: この値未満のconfidenceを持つ項目を質疑対象とする
        """
        self.confidence_threshold = confidence_threshold

    def extract_inquiries(
        self,
        fmt_doc: FMTDocument
    ) -> List[Inquiry]:
        """
        FMTDocumentから質疑項目を抽出

        Args:
            fmt_doc: FMTDocument

        Returns:
            質疑項目のリスト
        """
        inquiries = []

        # 見積項目から低信頼度項目を抽出
        for item in fmt_doc.estimate_items:
            if item.confidence and item.confidence < self.confidence_threshold:
                inquiry = self._create_inquiry_from_item(item)
                if inquiry:
                    inquiries.append(inquiry)

        logger.info(f"Extracted {len(inquiries)} inquiries from {len(fmt_doc.estimate_items)} items")
        return inquiries

    def _create_inquiry_from_item(self, item: EstimateItem) -> Inquiry:
        """
        EstimateItemから質疑を生成

        Args:
            item: 見積項目

        Returns:
            質疑項目
        """
        # 質疑文のテンプレート
        if item.quantity is None:
            question = f"「{item.name}」の数量について、仕様書に明記がありません。想定数量をご教示ください。"
            reason = "数量不明"
        elif item.unit_price is None:
            question = f"「{item.name}」について、過去の実績データがありません。適切な単価をご教示ください。"
            reason = "単価未確定"
        elif item.specification == "":
            question = f"「{item.name}」の仕様（型番、サイズ等）について、詳細をご教示ください。"
            reason = "仕様不明確"
        else:
            # 信頼度が低いその他の理由
            question = f"「{item.name}」について、仕様書の記載が不明確です。詳細をご教示ください。"
            reason = f"信頼度低（{item.confidence:.2f}）"

        return Inquiry(
            item_name=item.name,
            discipline=item.discipline.value if item.discipline else "未分類",
            question=question,
            reason=reason,
            confidence=item.confidence or 0.0,
            source_reference=item.source_reference or ""
        )

    def generate_inquiry_draft(
        self,
        inquiries: List[Inquiry],
        project_name: str = ""
    ) -> str:
        """
        質疑書ドラフトを生成（テキスト形式）

        Args:
            inquiries: 質疑項目のリスト
            project_name: 工事名

        Returns:
            質疑書テキスト
        """
        if not inquiries:
            return "質疑事項はありません。"

        # 工事区分別にグループ化
        by_discipline = {}
        for inquiry in inquiries:
            if inquiry.discipline not in by_discipline:
                by_discipline[inquiry.discipline] = []
            by_discipline[inquiry.discipline].append(inquiry)

        # テキスト生成
        lines = []
        lines.append("=" * 80)
        lines.append("質疑書（ドラフト）")
        lines.append("=" * 80)

        if project_name:
            lines.append(f"\n工事名: {project_name}")

        lines.append(f"\n質疑事項数: {len(inquiries)}件")
        lines.append("\n" + "-" * 80)

        question_no = 1
        for discipline, items in sorted(by_discipline.items()):
            lines.append(f"\n【{discipline}】")
            lines.append("")

            for inquiry in items:
                lines.append(f"質疑{question_no}:")
                lines.append(f"  項目: {inquiry.item_name}")
                lines.append(f"  質問: {inquiry.question}")
                lines.append(f"  理由: {inquiry.reason}（信頼度: {inquiry.confidence:.2f}）")
                lines.append("")
                question_no += 1

        lines.append("-" * 80)
        lines.append(f"\n※ 本質疑書はAIにより自動生成されたドラフトです。")
        lines.append(f"※ 信頼度{self.confidence_threshold}未満の項目を抽出しています。")

        return "\n".join(lines)

    def generate_inquiry_list(
        self,
        inquiries: List[Inquiry]
    ) -> List[Dict[str, Any]]:
        """
        質疑リストをJSON形式で生成

        Args:
            inquiries: 質疑項目のリスト

        Returns:
            質疑リスト（辞書のリスト）
        """
        return [
            {
                "item_name": inq.item_name,
                "discipline": inq.discipline,
                "question": inq.question,
                "reason": inq.reason,
                "confidence": inq.confidence,
                "source_reference": inq.source_reference
            }
            for inq in inquiries
        ]


if __name__ == "__main__":
    # テスト用
    from pipelines.schemas import DisciplineType
    from datetime import datetime

    # テストデータ
    test_items = [
        EstimateItem(
            item_no="1",
            level=2,
            name="白ガス管（ネジ接合）",
            specification="15A",
            quantity=None,  # 数量不明
            unit="m",
            unit_price=8990,
            discipline=DisciplineType.GAS,
            confidence=0.6,
            source_reference="仕様書p.12（数量記載なし）"
        ),
        EstimateItem(
            item_no="2",
            level=2,
            name="ガス漏れ警報器",
            specification="都市ガス用",
            quantity=12,
            unit="台",
            unit_price=None,  # 単価不明
            discipline=DisciplineType.GAS,
            confidence=0.5,
            source_reference="過去実績なし"
        ),
        EstimateItem(
            item_no="3",
            level=2,
            name="分岐コック",
            specification="",  # 仕様不明
            quantity=10,
            unit="個",
            unit_price=36010,
            discipline=DisciplineType.GAS,
            confidence=0.7,
            source_reference="仕様書p.15（サイズ記載なし）"
        ),
        EstimateItem(
            item_no="4",
            level=2,
            name="PE管",
            specification="50A",
            quantity=100,
            unit="m",
            unit_price=12200,
            discipline=DisciplineType.GAS,
            confidence=0.95,  # 高信頼度→質疑不要
            source_reference="仕様書p.10"
        ),
    ]

    from pipelines.schemas import ProjectInfo, FacilityType

    fmt_doc = FMTDocument(
        created_at=datetime.now().isoformat(),
        project_info=ProjectInfo(
            project_name="都立山崎高等学校 仮設校舎等の借入れ",
            client_name="東京都",
            location="東京都町田市",
            contract_period="2年間"
        ),
        facility_type=FacilityType.SCHOOL,
        disciplines=[DisciplineType.GAS],
        estimate_items=test_items
    )

    # 質疑抽出
    extractor = InquiryExtractor(confidence_threshold=0.8)
    inquiries = extractor.extract_inquiries(fmt_doc)

    print(f"\n抽出された質疑数: {len(inquiries)}")

    # 質疑書ドラフト生成
    draft = extractor.generate_inquiry_draft(
        inquiries,
        project_name=fmt_doc.project_info.project_name
    )

    print(draft)

    # JSON形式でも出力
    import json
    inquiry_list = extractor.generate_inquiry_list(inquiries)
    print(f"\n\n【JSON形式】")
    print(json.dumps(inquiry_list, ensure_ascii=False, indent=2))
