"""
参照見積書ベースの見積生成

仕様書の情報が不足している場合、実際の見積書（PDF）をテンプレートとして使用し、
類似案件の見積構造をそのまま適用する。
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv
from anthropic import Anthropic
from loguru import logger
import PyPDF2

from pipelines.schemas import (
    EstimateItem, DisciplineType, FMTDocument, ProjectInfo, FacilityType,
    CostType, OverheadCalculation
)
from pipelines.cost_tracker import record_cost


class EstimateFromReference:
    """
    参照見積書ベースの見積生成器

    仕様書に詳細情報がない場合、実際の見積書をテンプレートとして使用し、
    項目・単価・数量をそのまま適用する。
    """

    def __init__(self):
        load_dotenv()
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    def extract_estimate_from_pdf(
        self,
        pdf_path: str,
        discipline: DisciplineType
    ) -> List[EstimateItem]:
        """
        見積書PDFから詳細な項目・単価を抽出

        Args:
            pdf_path: 見積書PDFのパス
            discipline: 工事区分

        Returns:
            EstimateItemのリスト
        """
        logger.info(f"Extracting estimate from reference PDF: {pdf_path}")

        try:
            # PDFからテキストを抽出（全ページ対応）
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                total_pages = len(pdf_reader.pages)
                # 全ページを処理（制限なし）
                for page_num in range(total_pages):
                    text += pdf_reader.pages[page_num].extract_text() + "\n"

            logger.info(f"Extracted {len(text)} characters from reference PDF ({total_pages} pages)")

            # テキストがほとんど抽出できない場合はOCRを使用（スキャンPDFの可能性）
            # 閾値を緩和: 100 → 500文字
            if len(text.strip()) < 500:
                logger.warning(f"Text extraction yielded only {len(text)} chars, using OCR for reference PDF...")
                from pipelines.ocr_extractor import OCRExtractor
                ocr = OCRExtractor()

                # OCRで全ページ抽出
                items_data = ocr.extract_from_pdf(pdf_path)

                # OCR結果をテキストに変換
                text = "\n".join([
                    f"【項目】 {item.get('name', '')} | 仕様: {item.get('specification', '')} | "
                    f"数量: {item.get('quantity', '')} | 単位: {item.get('unit', '')} | "
                    f"単価: {item.get('unit_price', '')} | 金額: {item.get('amount', '')}"
                    for item in items_data
                ])

                logger.info(f"OCR extraction completed: {len(text)} characters, {len(items_data)} items")

            # LLMで構造化データに変換
            # テキスト制限を緩和: 最大60000文字（大規模PDF対応）
            prompt = f"""以下の見積書PDFから、見積項目を詳細に抽出してください。

見積書テキスト:
{text[:60000]}

【抽出する情報】
各見積項目について、以下の情報を正確に抽出してください：

1. **item_no**: 項番
2. **level**: 階層レベル（0=大項目、1=中項目、2=小項目、3=詳細項目）
3. **name**: 項目名
4. **specification**: 仕様（サイズ、型番、材質など）
5. **quantity**: 数量（数値）
6. **unit**: 単位（m、個、式、台など）
7. **unit_price**: 単価（円）
8. **amount**: 金額（円）
9. **cost_type**: 費用区分
   - "材料費": 材料単価 × 数量
   - "労務費": 作業員単価 × 人数 × 日数
   - "施工費": 工事範囲に応じた一式計上
   - "諸経費": 法定福利費、現場管理費など
   - "一式": 工種別一式金額
   - "機器費": キュービクル等の機器
   - "解体費": 既存撤去・切断
   - "掘削・埋戻し": 土工事
   - "復旧費": 舗装復旧等
10. **remarks**: 摘要

【重要な注意事項】
- 階層構造を正確に反映してください
- 単価と金額は必ず数値で抽出してください（カンマなし）
- 単位は必ず抽出してください
- 親項目（小計のみ）と子項目を区別してください
- 法定福利費（16.07%）も必ず抽出してください

【出力形式】
JSON配列形式で出力してください：

```json
[
  {{
    "item_no": "1",
    "level": 0,
    "name": "都市ガス設備工事",
    "specification": "",
    "quantity": null,
    "unit": "式",
    "unit_price": null,
    "amount": 11775000,
    "cost_type": "一式",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "基本工事費",
    "specification": "",
    "quantity": 1,
    "unit": "式",
    "unit_price": 450000,
    "amount": 450000,
    "cost_type": "施工費",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "配管工事費",
    "specification": "",
    "quantity": null,
    "unit": "",
    "unit_price": null,
    "amount": 5890000,
    "cost_type": "材料費",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 2,
    "name": "白ガス管（ネジ接合）",
    "specification": "15A",
    "quantity": 93,
    "unit": "m",
    "unit_price": 8990,
    "amount": 836070,
    "cost_type": "材料費",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "法定福利費",
    "specification": "",
    "quantity": null,
    "unit": "",
    "unit_price": null,
    "amount": 657263,
    "cost_type": "諸経費",
    "remarks": "工事費の16.07%"
  }}
]
```

必ずJSON形式で回答してください。"""

            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=16000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )

            # コスト記録
            record_cost(
                operation="見積抽出（参照PDF）",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={"file": Path(pdf_path).name, "discipline": discipline.value}
            )

            response_text = response.content[0].text
            logger.debug(f"LLM Response: {response_text[:500]}...")

            # JSONを抽出
            json_start = response_text.find('[')
            json_end = response_text.rfind(']') + 1

            if json_start == -1 or json_end == 0:
                logger.error("No JSON found in response")
                return []

            import json
            json_str = response_text[json_start:json_end]
            items_data = json.loads(json_str)

            logger.info(f"Extracted {len(items_data)} items from reference PDF")

            # EstimateItemに変換
            estimate_items = []
            for item_data in items_data:
                # cost_typeの変換
                cost_type_str = item_data.get("cost_type", "")
                cost_type = None
                if cost_type_str:
                    for ct in CostType:
                        if ct.value == cost_type_str:
                            cost_type = ct
                            break

                estimate_item = EstimateItem(
                    item_no=item_data.get("item_no", ""),
                    level=item_data.get("level", 0),
                    name=item_data.get("name", ""),
                    specification=item_data.get("specification", ""),
                    quantity=item_data.get("quantity"),
                    unit=item_data.get("unit", ""),
                    unit_price=item_data.get("unit_price"),
                    amount=item_data.get("amount"),
                    discipline=discipline,
                    cost_type=cost_type,
                    remarks=item_data.get("remarks", ""),
                    source_type="reference",
                    source_reference=Path(pdf_path).name
                )

                estimate_items.append(estimate_item)

            return estimate_items

        except Exception as e:
            logger.error(f"Error extracting estimate from PDF: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def adjust_quantities_from_spec(
        self,
        estimate_items: List[EstimateItem],
        spec_text: str
    ) -> List[EstimateItem]:
        """
        仕様書の情報に基づいて数量を調整

        Args:
            estimate_items: 参照見積書から抽出した項目
            spec_text: 仕様書テキスト

        Returns:
            数量調整後の項目リスト
        """
        logger.info("Adjusting quantities based on spec")

        # TODO: 仕様書から建物規模（面積、部屋数等）を抽出し、数量を調整
        # 現在は参照見積書の数量をそのまま使用

        return estimate_items

    def generate_estimate_from_reference(
        self,
        spec_pdf_path: str,
        reference_pdf_path: str,
        discipline: DisciplineType
    ) -> FMTDocument:
        """
        参照見積書をベースに見積書を生成

        Args:
            spec_pdf_path: 仕様書PDFのパス
            reference_pdf_path: 参照見積書PDFのパス
            discipline: 工事区分

        Returns:
            生成されたFMTDocument
        """
        logger.info(f"Generating estimate from reference: {reference_pdf_path}")

        # 1. 仕様書からプロジェクト情報を抽出
        from pipelines.estimate_extractor_v2 import EstimateExtractorV2
        extractor = EstimateExtractorV2()

        spec_text = extractor.extract_text_from_pdf(spec_pdf_path)
        project_info_dict = extractor.extract_project_info(spec_text)

        project_info = ProjectInfo(
            project_name=project_info_dict.get("project_name", ""),
            client_name=project_info_dict.get("client_name", ""),
            location=project_info_dict.get("location", ""),
            contract_period=project_info_dict.get("contract_period", "")
        )

        # 2. 参照見積書から詳細な項目・単価を抽出
        estimate_items = self.extract_estimate_from_pdf(
            reference_pdf_path,
            discipline
        )

        # 3. 仕様書の情報に基づいて数量を調整（オプション）
        estimate_items = self.adjust_quantities_from_spec(
            estimate_items,
            spec_text
        )

        # 4. FMTDocumentを作成
        fmt_doc = FMTDocument(
            created_at=datetime.now().isoformat(),
            project_info=project_info,
            facility_type=FacilityType.SCHOOL,
            disciplines=[discipline],
            estimate_items=estimate_items,
            metadata={
                "payment_terms": project_info_dict.get("payment_terms", "本紙記載内容のみ有効とする。"),
                "remarks": project_info_dict.get("remarks", "法定福利費を含む。"),
                "source": "参照見積書ベース",
                "reference_pdf": Path(reference_pdf_path).name
            }
        )

        logger.info(f"Generated FMTDocument with {len(estimate_items)} items")
        return fmt_doc


if __name__ == "__main__":
    # テスト実行
    import sys
    sys.path.insert(0, '.')

    generator = EstimateFromReference()

    spec_path = "test-files/仕様書【都立山崎高等学校仮設校舎等の借入れ】ord202403101060100130187c1e4d0.pdf"
    reference_path = "test-files/250918_送付状　見積書（都市ｶﾞｽ).pdf"

    if Path(spec_path).exists() and Path(reference_path).exists():
        print("\n" + "="*80)
        print("参照見積書ベースの見積生成テスト")
        print("="*80)

        # 見積書を生成
        fmt_doc = generator.generate_estimate_from_reference(
            spec_path,
            reference_path,
            DisciplineType.GAS
        )

        print(f"\n【生成結果】")
        print(f"  工事名: {fmt_doc.project_info.project_name}")
        print(f"  項目数: {len(fmt_doc.estimate_items)}")

        # 合計金額を計算
        total = sum(item.amount or 0 for item in fmt_doc.estimate_items)
        print(f"  合計金額: ¥{total:,.0f}")

        # 項目別統計
        cost_type_count = {}
        for item in fmt_doc.estimate_items:
            ct = item.cost_type.value if item.cost_type else "未分類"
            cost_type_count[ct] = cost_type_count.get(ct, 0) + 1

        print(f"\n【費用区分別項目数】")
        for ct, count in sorted(cost_type_count.items()):
            print(f"  {ct}: {count}項目")

        # 最初の20項目を表示
        print(f"\n【見積項目（最初の20項目）】")
        for i, item in enumerate(fmt_doc.estimate_items[:20]):
            indent = "  " * item.level
            spec_str = f" {item.specification}" if item.specification else ""
            qty_str = f" {item.quantity}{item.unit}" if item.quantity else ""
            price_str = f" @¥{item.unit_price:,.0f}" if item.unit_price else ""
            amount_str = f" = ¥{item.amount:,.0f}" if item.amount else ""
            ct = item.cost_type.value if item.cost_type else ""
            print(f"{indent}{item.name}{spec_str}{qty_str}{price_str}{amount_str} [{ct}]")

    else:
        print(f"❌ ファイルが見つかりません")
        print(f"   仕様書: {spec_path}")
        print(f"   参照見積書: {reference_path}")
