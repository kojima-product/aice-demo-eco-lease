"""
人間見積のパターン学習モジュール

過去の人間見積データから以下のパターンを学習します:
- 建物タイプ別の項目構成
- 面積あたりの数量比率
- 項目間の関係性
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict
from loguru import logger


class PatternLearner:
    """
    人間見積からパターンを学習するクラス

    KBに登録された人間見積データを分析し、
    - 建物タイプ別の標準項目構成
    - 面積あたりの数量係数
    - 仕様パターン
    を抽出します。
    """

    def __init__(self, kb_path: str = "kb/price_kb.json"):
        """
        Args:
            kb_path: KBファイルのパス
        """
        self.kb_path = Path(kb_path)
        self.kb_data = self._load_kb()
        self.patterns = {}

    def _load_kb(self) -> List[Dict[str, Any]]:
        """KBデータを読み込み"""
        if not self.kb_path.exists():
            logger.warning(f"KB file not found: {self.kb_path}")
            return []

        with open(self.kb_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def analyze_project_patterns(self) -> Dict[str, Any]:
        """
        プロジェクト別のパターンを分析

        Returns:
            プロジェクト別の分析結果
        """
        project_patterns = defaultdict(lambda: {
            "disciplines": defaultdict(list),
            "total_items": 0,
            "context_tags": set()
        })

        for item in self.kb_data:
            project = item.get("source_project", "unknown")
            discipline = item.get("discipline", "unknown")

            project_patterns[project]["disciplines"][discipline].append({
                "description": item.get("description", ""),
                "specification": item.get("features", {}).get("specification", ""),
                "unit": item.get("unit", ""),
                "unit_price": item.get("unit_price", 0),
                "quantity": item.get("features", {}).get("quantity", 0)
            })
            project_patterns[project]["total_items"] += 1

            for tag in item.get("context_tags", []):
                project_patterns[project]["context_tags"].add(tag)

        # set を list に変換（JSON出力用）
        for project in project_patterns:
            project_patterns[project]["context_tags"] = list(
                project_patterns[project]["context_tags"]
            )
            # defaultdict を dict に変換
            project_patterns[project]["disciplines"] = dict(
                project_patterns[project]["disciplines"]
            )

        return dict(project_patterns)

    def extract_discipline_patterns(self, discipline: str) -> Dict[str, Any]:
        """
        特定工事区分のパターンを抽出

        Args:
            discipline: 工事区分（例: "電気設備工事"）

        Returns:
            工事区分別パターン
        """
        items = [item for item in self.kb_data if item.get("discipline") == discipline]

        if not items:
            return {}

        # 項目名と仕様のパターンを集計
        item_specs = defaultdict(lambda: {"specs": [], "prices": [], "quantities": []})

        for item in items:
            desc = item.get("description", "")
            spec = item.get("features", {}).get("specification", "")
            price = item.get("unit_price", 0)
            qty = item.get("features", {}).get("quantity", 0)

            if desc:
                item_specs[desc]["specs"].append(spec)
                item_specs[desc]["prices"].append(price)
                item_specs[desc]["quantities"].append(qty)

        # 統計情報を計算
        patterns = {}
        for desc, data in item_specs.items():
            prices = [p for p in data["prices"] if p > 0]
            quantities = [q for q in data["quantities"] if q > 0]

            patterns[desc] = {
                "common_specs": list(set(data["specs"])),
                "avg_price": sum(prices) / len(prices) if prices else 0,
                "min_price": min(prices) if prices else 0,
                "max_price": max(prices) if prices else 0,
                "avg_quantity": sum(quantities) / len(quantities) if quantities else 0,
                "occurrence_count": len(data["specs"])
            }

        return {
            "discipline": discipline,
            "total_items": len(items),
            "unique_items": len(patterns),
            "item_patterns": patterns
        }

    def learn_building_type_patterns(self, building_type: str = None) -> Dict[str, Any]:
        """
        建物タイプ別のパターンを学習

        Args:
            building_type: 建物タイプ（None の場合は全て）

        Returns:
            建物タイプ別パターン
        """
        # context_tags から建物タイプを推定
        type_patterns = defaultdict(lambda: {
            "electrical": [],
            "plumbing": [],
            "mechanical": [],
            "gas": []
        })

        for item in self.kb_data:
            tags = item.get("context_tags", [])
            discipline = item.get("discipline", "")

            # タグから建物タイプを推定
            detected_type = "general"
            if "仮設" in tags:
                detected_type = "temporary_office"
            elif "学校" in tags:
                detected_type = "school"
            elif "事務所" in tags:
                detected_type = "office"

            if building_type and detected_type != building_type:
                continue

            # discipline を英語キーにマッピング
            disc_key = {
                "電気設備工事": "electrical",
                "衛生設備工事": "plumbing",
                "機械設備工事": "mechanical",
                "ガス設備工事": "gas"
            }.get(discipline, "other")

            if disc_key in type_patterns[detected_type]:
                type_patterns[detected_type][disc_key].append({
                    "name": item.get("description", ""),
                    "specification": item.get("features", {}).get("specification", ""),
                    "unit": item.get("unit", ""),
                    "unit_price": item.get("unit_price", 0),
                    "quantity": item.get("features", {}).get("quantity", 0)
                })

        return dict(type_patterns)

    def generate_improved_template(self, building_type: str) -> Dict[str, List[Dict]]:
        """
        学習したパターンから改善されたテンプレートを生成

        Args:
            building_type: 建物タイプ

        Returns:
            改善されたテンプレート
        """
        patterns = self.learn_building_type_patterns(building_type)

        if building_type not in patterns:
            logger.warning(f"No patterns found for building type: {building_type}")
            return {}

        building_patterns = patterns[building_type]
        improved_template = {}

        for disc_key, items in building_patterns.items():
            if not items:
                continue

            template_items = []
            for item in items:
                if not item.get("name"):
                    continue

                template_item = {
                    "name": item["name"],
                    "spec": item.get("specification", ""),
                    "unit": item.get("unit", "式"),
                    "learned_price": item.get("unit_price", 0),
                    "learned_quantity": item.get("quantity", 0),
                    "source": "human_estimate"
                }
                template_items.append(template_item)

            if template_items:
                improved_template[disc_key] = template_items

        return improved_template

    def compare_with_template(self, template_items: List[Dict], learned_items: List[Dict]) -> Dict[str, Any]:
        """
        テンプレート項目と学習項目を比較

        Args:
            template_items: テンプレートの項目リスト
            learned_items: 学習した項目リスト

        Returns:
            比較結果
        """
        template_names = {item.get("name", ""): item for item in template_items}
        learned_names = {item.get("name", ""): item for item in learned_items}

        # 一致する項目
        matched = []
        for name in template_names:
            if name in learned_names:
                matched.append({
                    "name": name,
                    "template": template_names[name],
                    "learned": learned_names[name]
                })

        # テンプレートにのみ存在
        template_only = [name for name in template_names if name not in learned_names]

        # 学習データにのみ存在（追加すべき項目）
        learned_only = [name for name in learned_names if name not in template_names]

        return {
            "matched_count": len(matched),
            "template_only_count": len(template_only),
            "learned_only_count": len(learned_only),
            "matched_items": matched,
            "template_only": template_only,
            "learned_only": learned_only,
            "suggestions": self._generate_suggestions(learned_only, learned_names)
        }

    def _generate_suggestions(self, learned_only: List[str], learned_names: Dict) -> List[Dict]:
        """
        テンプレートに追加すべき項目の提案を生成
        """
        suggestions = []
        for name in learned_only:
            item = learned_names.get(name, {})
            if item.get("learned_price", 0) > 0:
                suggestions.append({
                    "action": "add_to_template",
                    "item_name": name,
                    "specification": item.get("specification", ""),
                    "unit": item.get("unit", ""),
                    "reference_price": item.get("learned_price", 0),
                    "reason": "人間見積に存在するがテンプレートにない項目"
                })
        return suggestions

    def get_quantity_coefficients(self, discipline: str) -> Dict[str, float]:
        """
        面積あたりの数量係数を計算

        人間見積のデータから、面積あたりの標準数量を推定します。

        Args:
            discipline: 工事区分

        Returns:
            項目名 -> 面積あたり数量 のマッピング
        """
        # 仮設事務所の場合の標準床面積（推定）
        assumed_floor_area = 100  # 100㎡を仮定

        items = [item for item in self.kb_data if item.get("discipline") == discipline]

        coefficients = {}
        for item in items:
            desc = item.get("description", "")
            qty = item.get("features", {}).get("quantity", 0)
            unit = item.get("unit", "")

            if desc and qty > 0:
                # 面積あたりの数量を計算
                if unit in ["m", "ｍ"]:
                    # 配管・ケーブルは長さ
                    coefficients[desc] = qty / assumed_floor_area
                elif unit in ["箇所", "個", "ヶ所", "台"]:
                    # 機器類は個数
                    coefficients[desc] = qty / assumed_floor_area
                elif unit in ["面", "式"]:
                    # 一式は固定数
                    coefficients[desc] = qty  # 面積に依存しない

        return coefficients


def analyze_human_estimates():
    """
    人間見積の分析を実行
    """
    learner = PatternLearner()

    # プロジェクト別パターン
    project_patterns = learner.analyze_project_patterns()
    print("=== プロジェクト別パターン ===")
    for project, data in project_patterns.items():
        print(f"\nプロジェクト: {project}")
        print(f"  総項目数: {data['total_items']}")
        print(f"  タグ: {data['context_tags']}")
        for disc, items in data["disciplines"].items():
            print(f"  {disc}: {len(items)}項目")

    # 電気設備工事のパターン
    elec_patterns = learner.extract_discipline_patterns("電気設備工事")
    print("\n=== 電気設備工事パターン ===")
    print(f"総項目数: {elec_patterns.get('total_items', 0)}")
    print(f"ユニーク項目: {elec_patterns.get('unique_items', 0)}")

    # 改善テンプレート
    improved = learner.generate_improved_template("temporary_office")
    print("\n=== 改善テンプレート(仮設事務所) ===")
    for disc, items in improved.items():
        print(f"\n{disc}: {len(items)}項目")
        for item in items[:5]:  # 最初の5項目のみ表示
            print(f"  - {item['name']} ({item.get('spec', '')}) @{item.get('learned_price', 0)}")


if __name__ == "__main__":
    analyze_human_estimates()
