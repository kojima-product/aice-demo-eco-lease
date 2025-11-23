"""Estimate Generator - 見積明細を生成"""

import os
from typing import List, Dict, Any, Optional
from loguru import logger
from datetime import datetime

from pipelines.schemas import (
    FMTDocument,
    EstimateItem,
    DisciplineType,
    PriceReference
)
from pipelines.rag_price import PriceRAG


class EstimateGenerator:
    """見積明細を生成"""

    def __init__(self, use_llm: bool = True):
        """
        Args:
            use_llm: LLMを使用するかどうか（Trueの場合はAzure OpenAI使用）
        """
        self.use_llm = use_llm
        self.price_rag: Optional[PriceRAG] = None

        # LLM設定
        if self.use_llm:
            self._init_llm()

    def _init_llm(self):
        """Claude LLMを初期化"""
        try:
            from anthropic import Anthropic
            from dotenv import load_dotenv
            load_dotenv()

            self.client = Anthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY")
            )
            self.model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
            logger.info(f"Claude initialized: {self.model_name}")
        except Exception as e:
            logger.warning(f"Failed to initialize Claude: {e}")
            self.use_llm = False

    def set_price_rag(self, price_rag: PriceRAG):
        """PriceRAGを設定"""
        self.price_rag = price_rag

    def generate(self, fmt_doc: FMTDocument) -> FMTDocument:
        """
        FMTドキュメントから見積明細を生成

        Args:
            fmt_doc: FMTドキュメント

        Returns:
            見積明細が追加されたFMTドキュメント
        """
        logger.info("Generating estimate items")

        estimate_items = []

        # 工事区分ごとに見積を生成
        for idx, discipline in enumerate(fmt_doc.disciplines, start=1):
            # 大項目（工事区分）
            parent_item = EstimateItem(
                item_no=str(idx),
                name=f"{discipline.value}設備工事",
                level=0,
                discipline=discipline
            )
            estimate_items.append(parent_item)

            # 中項目・小項目を生成
            sub_items = self._generate_discipline_items(fmt_doc, discipline, parent_item.item_no)
            estimate_items.extend(sub_items)

            # 親項目の金額を集計
            parent_item.amount = sum(
                item.amount or 0 for item in sub_items
                if item.parent_item_no == parent_item.item_no and item.amount
            )

        # 諸経費を追加
        misc_items = self._generate_misc_items(estimate_items)
        estimate_items.extend(misc_items)

        fmt_doc.estimate_items = estimate_items

        # 合計金額を計算
        total_amount = sum(item.amount or 0 for item in estimate_items if item.level == 0)
        logger.info(f"Generated {len(estimate_items)} estimate items, total: ¥{total_amount:,.0f}")

        return fmt_doc

    def _generate_discipline_items(self, fmt_doc: FMTDocument,
                                   discipline: DisciplineType,
                                   parent_no: str) -> List[EstimateItem]:
        """
        特定の工事区分の見積項目を生成

        Args:
            fmt_doc: FMTドキュメント
            discipline: 工事区分
            parent_no: 親項番

        Returns:
            見積項目のリスト
        """
        items = []

        # 要求事項から項目を抽出
        requirements = fmt_doc.requirements or {}
        discipline_reqs = self._get_discipline_requirements(requirements, discipline)

        # 建物仕様から項目を抽出
        equipment_list = self._extract_equipment_from_buildings(fmt_doc, discipline)

        # LLMを使って項目を生成（optional）
        if self.use_llm:
            items = self._generate_items_with_llm(fmt_doc, discipline, parent_no, equipment_list)
        else:
            items = self._generate_items_rule_based(discipline, parent_no, equipment_list)

        return items

    def _generate_items_with_llm(self, fmt_doc: FMTDocument,
                                 discipline: DisciplineType,
                                 parent_no: str,
                                 equipment_list: List[Dict[str, Any]]) -> List[EstimateItem]:
        """LLMを使用して見積項目を生成"""

        # プロンプトを構築
        prompt = f"""あなたは見積作成の専門家です。以下の情報から{discipline.value}工事の見積項目を生成してください。

【案件情報】
案件名: {fmt_doc.project_info.project_name}
施設種別: {fmt_doc.facility_type.value}

【建物仕様】
"""
        for building in fmt_doc.building_specs:
            prompt += f"\n建物: {building.building_name}"
            prompt += f"\n延床面積: {building.total_area}㎡" if building.total_area else ""
            prompt += f"\n構造: {building.structure}" if building.structure else ""
            prompt += f"\n部屋数: {len(building.rooms)}"

        prompt += f"\n\n【{discipline.value}設備】\n"
        for equip in equipment_list[:20]:  # 最大20件
            prompt += f"- {equip.get('room_name', '')}: {equip.get('equipment', '')}\n"

        prompt += """
【出力形式】
JSON配列形式で以下のように出力してください：
[
  {
    "name": "項目名",
    "specification": "仕様",
    "quantity": 数量,
    "unit": "単位",
    "remarks": "備考"
  },
  ...
]

【重要】
- 必ずJSON配列（リスト）形式で出力してください
- 数量は概算で算出してください
- 単位は適切なもの（台、個、式、m、㎡など）を指定してください
- JSONのみを出力し、説明文は不要です
"""

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=16000,
                temperature=0.1,
                system="あなたは建設見積の専門家です。必ずJSON形式で回答してください。",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            import json
            # Claudeのレスポンスからテキストを抽出
            response_text = response.content[0].text

            # JSONを抽出（```json ... ``` がある場合に対応）
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            result = json.loads(response_text)

            # resultがリストの場合とディクショナリの場合の両方に対応
            if isinstance(result, list):
                items_data = result
            elif isinstance(result, dict):
                items_data = result.get('items', [])
            else:
                logger.warning(f"Unexpected result type: {type(result)}")
                items_data = []

            items = []
            for idx, item_data in enumerate(items_data, start=1):
                # RAGで価格を検索
                unit_price = None
                if self.price_rag:
                    query = f"{item_data.get('name', '')} {item_data.get('specification', '')}"
                    price_refs = self.price_rag.search(query, top_k=1, discipline=discipline)
                    if price_refs:
                        unit_price = price_refs[0].unit_price

                # デフォルト価格（RAGで見つからない場合）
                if unit_price is None:
                    unit_price = self._estimate_default_price(item_data.get('name', ''), discipline)

                quantity = item_data.get('quantity', 1)
                amount = unit_price * quantity if unit_price else None

                item = EstimateItem(
                    item_no=f"{parent_no}-{idx}",
                    name=item_data.get('name', ''),
                    specification=item_data.get('specification'),
                    quantity=quantity,
                    unit=item_data.get('unit', '式'),
                    unit_price=unit_price,
                    amount=amount,
                    remarks=item_data.get('remarks'),
                    parent_item_no=parent_no,
                    level=1,
                    discipline=discipline
                )
                items.append(item)

            return items

        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            # デバッグ用にレスポンスを表示
            try:
                logger.debug(f"Response text: {response_text[:500]}...")
            except:
                pass
            logger.info("Falling back to rule-based generation")
            return self._generate_items_rule_based(discipline, parent_no, equipment_list)

    def _generate_items_rule_based(self, discipline: DisciplineType,
                                   parent_no: str,
                                   equipment_list: List[Dict[str, Any]]) -> List[EstimateItem]:
        """ルールベースで見積項目を生成（LLMなし）"""

        items = []

        # 設備ごとに項目を作成
        for idx, equip in enumerate(equipment_list, start=1):
            equipment_name = equip.get('equipment', '')

            # RAGで価格を検索
            unit_price = None
            if self.price_rag:
                price_refs = self.price_rag.search(equipment_name, top_k=1, discipline=discipline)
                if price_refs:
                    unit_price = price_refs[0].unit_price

            # デフォルト価格
            if unit_price is None:
                unit_price = self._estimate_default_price(equipment_name, discipline)

            quantity = equip.get('quantity', 1)
            amount = unit_price * quantity if unit_price else None

            item = EstimateItem(
                item_no=f"{parent_no}-{idx}",
                name=equipment_name,
                specification=equip.get('specification'),
                quantity=quantity,
                unit=equip.get('unit', '式'),
                unit_price=unit_price,
                amount=amount,
                remarks=f"{equip.get('room_name', '')}",
                parent_item_no=parent_no,
                level=1,
                discipline=discipline
            )
            items.append(item)

        return items

    def _extract_equipment_from_buildings(self, fmt_doc: FMTDocument,
                                         discipline: DisciplineType) -> List[Dict[str, Any]]:
        """建物仕様から設備リストを抽出"""

        equipment_list = []

        for building in fmt_doc.building_specs:
            for room in building.rooms:
                for equip in room.equipment:
                    # 工事区分に該当するかチェック
                    if self._is_equipment_for_discipline(equip, discipline):
                        equipment_list.append({
                            'room_name': room.room_name,
                            'equipment': equip,
                            'area': room.area,
                            'quantity': 1,
                            'unit': '式'
                        })

        return equipment_list

    def _is_equipment_for_discipline(self, equipment: str, discipline: DisciplineType) -> bool:
        """設備が指定の工事区分に該当するかチェック"""

        keywords = {
            DisciplineType.ELECTRICAL: ['照明', '電灯', 'コンセント', '分電盤', '電気'],
            DisciplineType.HVAC: ['空調', 'エアコン', '換気'],
            DisciplineType.PLUMBING: ['給水', '給湯', '排水', '便器', '洗面'],
            DisciplineType.GAS: ['ガス'],
            DisciplineType.FIRE_PROTECTION: ['消防', '消火', 'スプリンクラー'],
        }

        equip_lower = equipment.lower()
        for kw in keywords.get(discipline, []):
            if kw in equip_lower:
                return True

        return False

    def _estimate_default_price(self, item_name: str, discipline: DisciplineType) -> float:
        """デフォルト価格を推定（RAGで見つからない場合）"""

        # 簡易的な価格マッピング
        default_prices = {
            DisciplineType.ELECTRICAL: 50000,
            DisciplineType.HVAC: 150000,
            DisciplineType.PLUMBING: 80000,
            DisciplineType.GAS: 100000,
            DisciplineType.FIRE_PROTECTION: 120000,
        }

        return default_prices.get(discipline, 100000)

    def _get_discipline_requirements(self, requirements: Dict[str, Any],
                                    discipline: DisciplineType) -> List[str]:
        """工事区分に該当する要求事項を取得"""

        mapping = {
            DisciplineType.ELECTRICAL: 'electrical',
            DisciplineType.MECHANICAL: 'mechanical',
            DisciplineType.HVAC: 'hvac',
            DisciplineType.PLUMBING: 'plumbing',
            DisciplineType.GAS: 'gas',
            DisciplineType.FIRE_PROTECTION: 'fire_protection',
        }

        key = mapping.get(discipline)
        return requirements.get(key, []) if key else []

    def _generate_misc_items(self, estimate_items: List[EstimateItem]) -> List[EstimateItem]:
        """諸経費を生成"""

        # 工事費の合計
        construction_total = sum(
            item.amount or 0 for item in estimate_items if item.level == 0
        )

        misc_items = []

        # 諸経費の項番を決定
        next_no = len([item for item in estimate_items if item.level == 0]) + 1

        # 法定福利費（工事費の5%）
        welfare_amount = construction_total * 0.05
        misc_items.append(EstimateItem(
            item_no=str(next_no),
            name="法定福利費",
            specification="社会保険料等",
            amount=welfare_amount,
            unit="式",
            level=0
        ))

        next_no += 1

        # 現場管理費（工事費の3%）
        management_amount = construction_total * 0.03
        misc_items.append(EstimateItem(
            item_no=str(next_no),
            name="現場管理費",
            specification="現場経費",
            amount=management_amount,
            unit="式",
            level=0
        ))

        next_no += 1

        # 一般管理費（工事費の2%）
        general_amount = construction_total * 0.02
        misc_items.append(EstimateItem(
            item_no=str(next_no),
            name="一般管理費",
            specification="本社経費",
            amount=general_amount,
            unit="式",
            level=0
        ))

        return misc_items
