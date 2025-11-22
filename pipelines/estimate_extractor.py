"""仕様書から見積項目を抽出するLLMベースの機能"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv
from anthropic import Anthropic
from loguru import logger
import PyPDF2

from pipelines.schemas import EstimateItem, DisciplineType, FMTDocument, ProjectInfo, FacilityType


class EstimateExtractor:
    """仕様書PDFから見積項目を抽出"""

    def __init__(self):
        load_dotenv()
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    def extract_text_from_pdf(self, pdf_path: str, max_pages: int = None) -> str:
        """PDFからテキストを抽出（ページ制限なし）"""
        logger.info(f"Extracting text from PDF: {pdf_path}")

        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                total_pages = len(pdf_reader.pages)
                pages_to_read = total_pages if max_pages is None else min(total_pages, max_pages)

                text = ""
                for page_num in range(pages_to_read):
                    page = pdf_reader.pages[page_num]
                    text += page.extract_text() + "\n"

                logger.info(f"Extracted {len(text)} characters from {pages_to_read}/{total_pages} pages")
                return text
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            raise

    def extract_estimate_items(self, spec_text: str, discipline: DisciplineType) -> List[Dict[str, Any]]:
        """
        仕様書テキストから見積項目を抽出

        Args:
            spec_text: 仕様書のテキスト
            discipline: 工事区分（電気、機械、ガスなど）

        Returns:
            見積項目のリスト
        """
        logger.info(f"Extracting estimate items for discipline: {discipline}")

        discipline_map = {
            DisciplineType.ELECTRICAL: "電気設備工事",
            DisciplineType.MECHANICAL: "機械設備工事",
            DisciplineType.HVAC: "空調設備工事",
            DisciplineType.PLUMBING: "衛生設備工事",
            DisciplineType.GAS: "都市ガス設備工事"
        }

        discipline_name = discipline_map.get(discipline, "設備工事")

        prompt = f"""以下の入札仕様書から、{discipline_name}に関連する見積項目を抽出してください。

仕様書テキスト:
{spec_text[:60000]}

【抽出する項目】
各見積項目について、以下の情報を抽出してください：
1. 項目名（name）: 工事項目の名称
2. 仕様（specification）: サイズ、型番、材質などの仕様
3. 数量（quantity）: 数値または null
4. 単位（unit）: m, 個, 式, 台など
5. 階層レベル（level）: 0=親項目, 1=子項目, 2=孫項目
6. 項目番号（item_no）: あれば記載、なければ空文字

【注意事項】
- {discipline_name}に関連する項目のみを抽出してください
- 配管、配線、機器、材料、工事費など、具体的な項目を抽出してください
- 数量が記載されていない場合は null を設定してください
- 階層構造を適切に設定してください（大項目→中項目→小項目）

【出力形式】
JSON配列形式で出力してください：
```json
[
  {{
    "item_no": "1",
    "level": 0,
    "name": "{discipline_name}",
    "specification": "",
    "quantity": null,
    "unit": "",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "配管工事費",
    "specification": "",
    "quantity": null,
    "unit": "",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 2,
    "name": "白ガス管（ネジ接合）",
    "specification": "15A",
    "quantity": 93,
    "unit": "m",
    "remarks": ""
  }}
]
```

必ずJSON形式で回答してください。"""

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=8000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text
            logger.debug(f"LLM Response: {response_text[:500]}...")

            # JSONを抽出
            json_start = response_text.find('[')
            json_end = response_text.rfind(']') + 1

            if json_start == -1 or json_end == 0:
                logger.error("No JSON found in response")
                return []

            json_str = response_text[json_start:json_end]
            items_data = json.loads(json_str)

            logger.info(f"Extracted {len(items_data)} items for {discipline_name}")
            return items_data

        except Exception as e:
            logger.error(f"Error extracting estimate items: {e}")
            return []

    def extract_project_info(self, spec_text: str) -> Dict[str, str]:
        """仕様書から工事情報を抽出"""
        logger.info("Extracting project information")

        prompt = f"""以下の入札仕様書から、工事の基本情報を抽出してください。

仕様書テキスト:
{spec_text[:60000]}

【抽出する項目】
1. 工事名（project_name）
2. 工事場所（location）
3. リース期間（contract_period）
4. 決済条件（payment_terms）
5. 備考（remarks）
6. 顧客名（client_name）

【出力形式】
JSON形式で出力してください：
```json
{{
  "project_name": "都立山崎高校仮設校舎 設備工事",
  "location": "東京都町田市山崎町1453番地1",
  "contract_period": "25ヶ月（2026.8.1～2028.8.31）見積有効期間6ヶ月",
  "payment_terms": "本紙記載内容のみ有効とする。",
  "remarks": "法定福利費を含む。",
  "client_name": ""
}}
```

必ずJSON形式で回答してください。"""

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=2000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text

            # JSONを抽出
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1

            if json_start == -1 or json_end == 0:
                logger.error("No JSON found in response")
                return {}

            json_str = response_text[json_start:json_end]
            project_info = json.loads(json_str)

            logger.info(f"Extracted project info: {project_info.get('project_name', 'N/A')}")
            return project_info

        except Exception as e:
            logger.error(f"Error extracting project info: {e}")
            return {}

    def create_fmt_document_from_spec(
        self,
        spec_pdf_path: str,
        disciplines: List[DisciplineType]
    ) -> FMTDocument:
        """
        仕様書PDFからFMTDocumentを生成

        Args:
            spec_pdf_path: 仕様書PDFのパス
            disciplines: 抽出する工事区分のリスト

        Returns:
            生成されたFMTDocument
        """
        logger.info(f"Creating FMTDocument from spec: {spec_pdf_path}")

        # PDFからテキストを抽出
        spec_text = self.extract_text_from_pdf(spec_pdf_path)

        # プロジェクト情報を抽出
        project_info_dict = self.extract_project_info(spec_text)

        project_info = ProjectInfo(
            project_name=project_info_dict.get("project_name", ""),
            client_name=project_info_dict.get("client_name", ""),
            location=project_info_dict.get("location", ""),
            contract_period=project_info_dict.get("contract_period", "")
        )

        # 各工事区分の見積項目を抽出
        all_items = []
        for discipline in disciplines:
            items_data = self.extract_estimate_items(spec_text, discipline)

            for item_data in items_data:
                estimate_item = EstimateItem(
                    item_no=item_data.get("item_no", ""),
                    level=item_data.get("level", 0),
                    name=item_data.get("name", ""),
                    specification=item_data.get("specification", ""),
                    quantity=item_data.get("quantity"),
                    unit=item_data.get("unit", ""),
                    unit_price=None,  # 単価は別途設定
                    amount=None,  # 金額は別途計算
                    discipline=discipline
                )
                all_items.append(estimate_item)

        # FMTDocumentを作成
        fmt_doc = FMTDocument(
            created_at=datetime.now().isoformat(),
            project_info=project_info,
            facility_type=FacilityType.SCHOOL,  # デフォルト値、後で変更可能
            disciplines=disciplines,
            estimate_items=all_items,
            metadata={
                "payment_terms": project_info_dict.get("payment_terms", "本紙記載内容のみ有効とする。"),
                "remarks": project_info_dict.get("remarks", "法定福利費を含む。"),
                "source": "LLM自動抽出"
            }
        )

        logger.info(f"Created FMTDocument with {len(all_items)} items")
        return fmt_doc


if __name__ == "__main__":
    # テスト実行
    extractor = EstimateExtractor()

    spec_path = "test-files/仕様書【都立山崎高等学校仮設校舎等の借入れ】ord202403101060100130187c1e4d0.pdf"

    if Path(spec_path).exists():
        fmt_doc = extractor.create_fmt_document_from_spec(
            spec_path,
            disciplines=[DisciplineType.GAS]
        )

        print(f"✅ 抽出完了:")
        print(f"   工事名: {fmt_doc.project_info.project_name}")
        print(f"   場所: {fmt_doc.project_info.location}")
        print(f"   見積項目数: {len(fmt_doc.estimate_items)}")

        # 最初の10項目を表示
        print("\n【抽出された項目（最初の10項目）】")
        for i, item in enumerate(fmt_doc.estimate_items[:10]):
            indent = "  " * item.level
            print(f"{indent}{item.name} {item.specification} {item.quantity}{item.unit}")
    else:
        print(f"❌ 仕様書が見つかりません: {spec_path}")
