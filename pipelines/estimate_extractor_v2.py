"""
仕様書から見積項目を抽出するLLMベースの機能（v2）
file_logic.md分析に基づく改善版
"""

import os
import json
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


class EstimateExtractorV2:
    """仕様書PDFから見積項目を抽出（file_logic.md分析に基づく改善版）"""

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
        仕様書テキストから見積項目を抽出（file_logic.md分析に基づく改善版）

        Args:
            spec_text: 仕様書のテキスト
            discipline: 工事区分（電気、機械、ガスなど）

        Returns:
            見積項目のリスト（費用区分・計算ロジック付き）
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

        # file_logic.md分析結果を反映したプロンプト
        prompt = f"""あなたは建設業の見積作成の専門家です。以下の入札仕様書から、{discipline_name}に関連する見積項目を抽出してください。

# 重要な前提知識（file_logic.md分析より）

見積書は以下のロジックで構成されます：

1. **材料費**: 材料単価 × 数量（ケーブル、照明器具、配管、分電盤など）
2. **労務費**: 作業員単価 × 人数 × 日数（配線工事、配管工事など）
3. **施工費**: 工事範囲に応じた一式計上（掘削、埋設、撤去など）
4. **諸経費**: 法定福利費（16.07%）、現場管理費など
5. **一式**: 工種別一式金額（詳細内訳なし）

# 仕様書テキスト

{spec_text[:60000]}

# 抽出指示

## 1. 階層構造（4階層）
- **level 0**: 大項目（例: 都市ガス設備工事、電気設備工事）
- **level 1**: 中項目（例: 配管工事費、基本工事費、ガス栓等材料費）
- **level 2**: 小項目（例: 白ガス管、カラー鋼管、PE管）
- **level 3**: 詳細項目（例: 15A、20A、25A など規格別）

## 2. 費用区分（cost_type）の判定基準

**【材料費】**: 具体的な材料名＋規格＋数量が明示されている
- 例: 白ガス管 15A、LED照明、ケーブル CVT60sq

**【労務費】**: 作業内容＋工事費と記載
- 例: 配線工事費、配管工事費、取付工事費

**【施工費】**: 工事範囲が広く一式計上
- 例: 掘削工事一式、埋設工事一式、架台工事

**【諸経費】**: 法定福利費、現場管理費、安全管理費
- 例: 法定福利費（16.07%）

**【一式】**: 詳細内訳なく工種単位で計上
- 例: 基礎工事一式、既存設備撤去一式

**【機器費】**: 機器単体の価格
- 例: キュービクル、分電盤、受変電設備

**【解体費】**: 既存設備の撤去・切断
- 例: 配管撤去費、既存設備解体費

**【掘削・埋戻し】**: 土工事関連
- 例: 掘削費、埋戻し費

**【復旧費】**: 舗装復旧、壁補修等
- 例: 舗装復旧費、穴補修費、コンクリート復旧

## 3. 抽出する項目フィールド

各見積項目について、以下の情報を抽出してください：

- **item_no**: 項番（あれば記載、なければ空文字）
- **level**: 階層レベル（0-3）
- **name**: 項目名称
- **specification**: 仕様（サイズ、型番、材質など）
- **quantity**: 数量（数値または null）
- **unit**: 単位（m, 個, 式, 台, 箇所, 人日など）
- **cost_type**: 費用区分（"材料費", "労務費", "施工費", "諸経費", "一式", "機器費", "解体費", "掘削・埋戻し", "復旧費"）
- **remarks**: 摘要・備考

## 4. 注意事項

- {discipline_name}に関連する項目のみを抽出してください
- 階層構造を適切に設定してください（大項目→中項目→小項目→詳細項目）
- 費用区分を必ず判定してください
- 数量が記載されていない場合は null を設定
- 仕様書に明記されている項目を優先し、推測は最小限に
- 法定福利費は必ず諸経費として抽出

# 出力形式

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
    "cost_type": "一式",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "配管工事費",
    "specification": "",
    "quantity": null,
    "unit": "",
    "cost_type": "材料費",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 2,
    "name": "白ガス管（ネジ接合）",
    "specification": "",
    "quantity": null,
    "unit": "",
    "cost_type": "材料費",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 3,
    "name": "白ガス管（ネジ接合）",
    "specification": "15A",
    "quantity": 93,
    "unit": "m",
    "cost_type": "材料費",
    "remarks": ""
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "解体費",
    "specification": "",
    "quantity": 1,
    "unit": "式",
    "cost_type": "解体費",
    "remarks": "既存設備撤去"
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "法定福利費",
    "specification": "",
    "quantity": null,
    "unit": "",
    "cost_type": "諸経費",
    "remarks": "工事費の16.07%"
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

            # コスト記録
            record_cost(
                operation="見積抽出（v2）",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={"discipline": discipline.value}
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

            # コスト記録
            record_cost(
                operation="プロジェクト情報抽出",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={}
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

    def calculate_overheads(
        self,
        estimate_items: List[EstimateItem],
        overhead_rate: float = 0.1607
    ) -> List[OverheadCalculation]:
        """
        諸経費を計算（file_logic.md分析より: 法定福利費16.07%）

        Args:
            estimate_items: 見積項目リスト
            overhead_rate: 諸経費率（デフォルト: 16.07%）

        Returns:
            諸経費計算結果
        """
        logger.info("Calculating overhead costs")

        # 工事費の合計を計算（材料費+労務費+施工費+機器費）
        base_amount = 0.0
        for item in estimate_items:
            if item.cost_type in [
                CostType.MATERIAL,
                CostType.LABOR,
                CostType.CONSTRUCTION,
                CostType.EQUIPMENT,
                CostType.DEMOLITION,
                CostType.EXCAVATION,
                CostType.RESTORATION
            ]:
                if item.amount:
                    base_amount += item.amount

        # 法定福利費を計算
        overhead_amount = base_amount * overhead_rate

        overhead = OverheadCalculation(
            name="法定福利費",
            rate=overhead_rate,
            base_amount=base_amount,
            amount=overhead_amount,
            formula=f"工事費 ¥{base_amount:,.0f} × {overhead_rate*100:.2f}%",
            remarks="file_logic.md分析より: 都市ガス見積書に記載の標準率"
        )

        logger.info(f"Calculated overhead: ¥{overhead_amount:,.0f}")
        return [overhead]

    def create_fmt_document_from_spec(
        self,
        spec_pdf_path: str,
        disciplines: List[DisciplineType]
    ) -> FMTDocument:
        """
        仕様書PDFからFMTDocumentを生成（file_logic.md分析に基づく改善版）

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
                    unit_price=None,  # 単価は別途設定（RAGで付与）
                    amount=None,  # 金額は別途計算
                    discipline=discipline,
                    cost_type=cost_type,
                    remarks=item_data.get("remarks", "")
                )
                all_items.append(estimate_item)

        # 諸経費を計算（法定福利費16.07%）
        overhead_calcs = self.calculate_overheads(all_items, overhead_rate=0.1607)

        # FMTDocumentを作成
        fmt_doc = FMTDocument(
            created_at=datetime.now().isoformat(),
            project_info=project_info,
            facility_type=FacilityType.SCHOOL,  # デフォルト値、後で変更可能
            disciplines=disciplines,
            estimate_items=all_items,
            overhead_calculations=overhead_calcs,
            metadata={
                "payment_terms": project_info_dict.get("payment_terms", "本紙記載内容のみ有効とする。"),
                "remarks": project_info_dict.get("remarks", "法定福利費を含む。"),
                "source": "LLM自動抽出 v2.0 (file_logic.md分析ベース)",
                "extraction_version": "2.0"
            }
        )

        logger.info(f"Created FMTDocument with {len(all_items)} items")
        return fmt_doc


if __name__ == "__main__":
    # テスト実行
    extractor = EstimateExtractorV2()

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

        # 費用区分別の項目数を集計
        cost_type_count = {}
        for item in fmt_doc.estimate_items:
            ct = item.cost_type.value if item.cost_type else "未分類"
            cost_type_count[ct] = cost_type_count.get(ct, 0) + 1

        print("\n【費用区分別項目数】")
        for ct, count in cost_type_count.items():
            print(f"   {ct}: {count}項目")

        # 最初の15項目を表示
        print("\n【抽出された項目（最初の15項目）】")
        for i, item in enumerate(fmt_doc.estimate_items[:15]):
            indent = "  " * item.level
            ct = item.cost_type.value if item.cost_type else "未分類"
            qty_str = f"{item.quantity}{item.unit}" if item.quantity else ""
            print(f"{indent}{item.name} {item.specification} {qty_str} [{ct}]")

        # 諸経費を表示
        print("\n【諸経費計算】")
        for overhead in fmt_doc.overhead_calculations:
            print(f"   {overhead.name}: ¥{overhead.amount:,.0f}")
            print(f"   計算式: {overhead.formula}")
    else:
        print(f"❌ 仕様書が見つかりません: {spec_path}")
