"""
類似案件検索モジュール

過去の見積案件から類似プロジェクトを検索し、
精度検証や見積比較に活用します。
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import defaultdict
from loguru import logger


class SimilarProjectSearch:
    """
    類似案件検索クラス

    KBから類似のプロジェクトを検索し、
    - 建物タイプの類似性
    - 規模（面積・階数）の類似性
    - 工事区分の類似性
    を基に類似度スコアを計算します。
    """

    def __init__(self, kb_path: str = "kb/price_kb.json"):
        """
        Args:
            kb_path: KBファイルのパス
        """
        self.kb_path = Path(kb_path)
        self.kb_data = self._load_kb()
        self.project_index = self._build_project_index()

    def _load_kb(self) -> List[Dict[str, Any]]:
        """KBデータを読み込み"""
        if not self.kb_path.exists():
            logger.warning(f"KB file not found: {self.kb_path}")
            return []

        with open(self.kb_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _build_project_index(self) -> Dict[str, Dict[str, Any]]:
        """
        プロジェクト別のインデックスを構築
        """
        index = defaultdict(lambda: {
            "items": [],
            "disciplines": set(),
            "context_tags": set(),
            "total_amount": 0.0,
            "item_count": 0
        })

        for item in self.kb_data:
            project = item.get("source_project", "unknown")
            discipline = item.get("discipline", "")
            price = item.get("unit_price", 0) or 0
            qty = item.get("features", {}).get("quantity", 0) or 0

            index[project]["items"].append(item)
            if discipline:
                index[project]["disciplines"].add(discipline)
            for tag in item.get("context_tags", []):
                index[project]["context_tags"].add(tag)
            index[project]["total_amount"] += price * qty
            index[project]["item_count"] += 1

        # set を list に変換
        for project in index:
            index[project]["disciplines"] = list(index[project]["disciplines"])
            index[project]["context_tags"] = list(index[project]["context_tags"])

        return dict(index)

    def search_similar_projects(
        self,
        target_building_type: str = None,
        target_disciplines: List[str] = None,
        target_floor_area: float = None,
        target_context_tags: List[str] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        類似プロジェクトを検索

        Args:
            target_building_type: 建物タイプ（例: "仮設事務所"）
            target_disciplines: 工事区分リスト
            target_floor_area: 床面積（㎡）
            target_context_tags: コンテキストタグ
            top_k: 返す件数

        Returns:
            類似プロジェクトのリスト（スコア順）
        """
        if not self.project_index:
            return []

        results = []

        for project_name, project_data in self.project_index.items():
            score = 0.0
            match_reasons = []

            # 建物タイプの類似性（タグベース）
            if target_building_type:
                project_tags = project_data.get("context_tags", [])
                type_keywords = self._get_type_keywords(target_building_type)
                for keyword in type_keywords:
                    if any(keyword in tag for tag in project_tags):
                        score += 0.3
                        match_reasons.append(f"建物タイプ一致: {keyword}")
                        break

            # 工事区分の類似性
            if target_disciplines:
                project_disciplines = set(project_data.get("disciplines", []))
                target_disc_set = set(target_disciplines)
                overlap = len(project_disciplines & target_disc_set)
                if overlap > 0:
                    disc_score = 0.3 * (overlap / max(len(target_disc_set), 1))
                    score += disc_score
                    match_reasons.append(f"工事区分一致: {overlap}/{len(target_disc_set)}")

            # コンテキストタグの類似性
            if target_context_tags:
                project_tags = set(project_data.get("context_tags", []))
                target_tags = set(target_context_tags)
                overlap = len(project_tags & target_tags)
                if overlap > 0:
                    tag_score = 0.2 * (overlap / max(len(target_tags), 1))
                    score += tag_score
                    match_reasons.append(f"タグ一致: {overlap}件")

            # 項目数による類似性（規模の指標として）
            item_count = project_data.get("item_count", 0)
            if item_count > 10:
                score += 0.1
                match_reasons.append(f"項目数: {item_count}")

            if score > 0:
                results.append({
                    "project_name": project_name,
                    "similarity_score": round(score, 3),
                    "match_reasons": match_reasons,
                    "disciplines": project_data.get("disciplines", []),
                    "context_tags": project_data.get("context_tags", []),
                    "item_count": item_count,
                    "total_amount": project_data.get("total_amount", 0)
                })

        # スコア順でソート
        results.sort(key=lambda x: x["similarity_score"], reverse=True)

        return results[:top_k]

    def _get_type_keywords(self, building_type: str) -> List[str]:
        """
        建物タイプからキーワードを取得
        """
        type_keywords = {
            "temporary_office": ["仮設", "事務所", "プレハブ"],
            "school": ["学校", "校舎", "教室"],
            "office": ["事務所", "オフィス"],
            "warehouse": ["倉庫", "工場"],
            "hospital": ["病院", "医療", "クリニック"],
            "retail": ["店舗", "商業"],
        }
        return type_keywords.get(building_type, [building_type])

    def get_project_details(self, project_name: str) -> Dict[str, Any]:
        """
        プロジェクトの詳細情報を取得

        Args:
            project_name: プロジェクト名

        Returns:
            プロジェクト詳細
        """
        if project_name not in self.project_index:
            return {}

        project_data = self.project_index[project_name]

        # 工事区分別の集計
        discipline_summary = defaultdict(lambda: {"items": [], "total": 0})

        for item in project_data.get("items", []):
            disc = item.get("discipline", "その他")
            price = item.get("unit_price", 0) or 0
            qty = item.get("features", {}).get("quantity", 0) or 0
            amount = price * qty

            discipline_summary[disc]["items"].append({
                "name": item.get("description", ""),
                "spec": item.get("features", {}).get("specification", ""),
                "unit": item.get("unit", ""),
                "unit_price": price,
                "quantity": qty,
                "amount": amount
            })
            discipline_summary[disc]["total"] += amount

        return {
            "project_name": project_name,
            "disciplines": project_data.get("disciplines", []),
            "context_tags": project_data.get("context_tags", []),
            "item_count": project_data.get("item_count", 0),
            "total_amount": project_data.get("total_amount", 0),
            "discipline_breakdown": dict(discipline_summary)
        }

    def compare_estimates(
        self,
        current_items: List[Dict[str, Any]],
        reference_project: str
    ) -> Dict[str, Any]:
        """
        現在の見積と参照プロジェクトを比較

        Args:
            current_items: 現在の見積項目リスト
            reference_project: 参照プロジェクト名

        Returns:
            比較結果
        """
        if reference_project not in self.project_index:
            return {"error": f"Project not found: {reference_project}"}

        ref_data = self.project_index[reference_project]
        ref_items = ref_data.get("items", [])

        # 項目名でマッチング
        current_names = {item.get("name", ""): item for item in current_items}
        ref_names = {item.get("description", ""): item for item in ref_items}

        # 一致する項目
        matched = []
        for name in current_names:
            if name in ref_names:
                curr_item = current_names[name]
                ref_item = ref_names[name]
                matched.append({
                    "name": name,
                    "current_price": curr_item.get("unit_price", 0),
                    "reference_price": ref_item.get("unit_price", 0),
                    "price_diff": (curr_item.get("unit_price", 0) or 0) - (ref_item.get("unit_price", 0) or 0)
                })

        # 現在のみの項目
        current_only = [name for name in current_names if name not in ref_names]

        # 参照のみの項目
        reference_only = [name for name in ref_names if name not in current_names]

        # 合計金額の比較
        current_total = sum((item.get("amount", 0) or 0) for item in current_items)
        ref_total = ref_data.get("total_amount", 0)

        return {
            "reference_project": reference_project,
            "current_item_count": len(current_items),
            "reference_item_count": len(ref_items),
            "matched_count": len(matched),
            "current_only_count": len(current_only),
            "reference_only_count": len(reference_only),
            "current_total": current_total,
            "reference_total": ref_total,
            "total_diff": current_total - ref_total,
            "total_diff_percent": ((current_total - ref_total) / ref_total * 100) if ref_total > 0 else 0,
            "matched_items": matched[:10],  # 最初の10件のみ
            "missing_from_current": reference_only[:10],
            "extra_in_current": current_only[:10]
        }


def search_and_compare(building_type: str, disciplines: List[str]) -> Dict[str, Any]:
    """
    類似案件を検索して比較

    Args:
        building_type: 建物タイプ
        disciplines: 工事区分リスト

    Returns:
        検索結果と比較情報
    """
    searcher = SimilarProjectSearch()

    # 類似プロジェクトを検索
    similar = searcher.search_similar_projects(
        target_building_type=building_type,
        target_disciplines=disciplines,
        top_k=3
    )

    result = {
        "building_type": building_type,
        "target_disciplines": disciplines,
        "similar_projects": similar
    }

    # 最も類似度が高いプロジェクトの詳細を取得
    if similar:
        top_project = similar[0]["project_name"]
        result["top_project_details"] = searcher.get_project_details(top_project)

    return result


if __name__ == "__main__":
    # Test
    result = search_and_compare(
        building_type="temporary_office",
        disciplines=["電気設備工事", "機械設備工事"]
    )

    print("=== 類似案件検索結果 ===")
    print(f"対象建物タイプ: {result['building_type']}")
    print(f"対象工事区分: {result['target_disciplines']}")

    print("\n=== 類似プロジェクト ===")
    for proj in result.get("similar_projects", []):
        print(f"\n{proj['project_name']}")
        print(f"  類似度: {proj['similarity_score']}")
        print(f"  理由: {', '.join(proj['match_reasons'])}")
        print(f"  項目数: {proj['item_count']}")

    if result.get("top_project_details"):
        details = result["top_project_details"]
        print(f"\n=== 最類似プロジェクト詳細: {details['project_name']} ===")
        print(f"総項目数: {details['item_count']}")
        print(f"推定総額: ¥{details['total_amount']:,.0f}")
