"""
建物タイプ別見積テンプレート

建物タイプごとに標準的な設備構成と数量計算式を定義。
人間見積のデータを基に、詳細な項目を自動生成する。
"""

from typing import Dict, List, Any


# Building type templates based on actual human estimates
BUILDING_TEMPLATES: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    # 人間見積実績（大洲バイオマス 82.69㎡）から逆算した正確な係数
    "仮設事務所": {
        "electrical": [
            # 幹線設備（人間見積実績ベース）
            {"name": "600V 架橋ポリエチレンケーブル", "spec": "CV8sq-3C", "qty_per_sqm": 0.15, "unit": "m", "category": "幹線設備"},  # 実績: 12m/82㎡
            {"name": "600V 架橋ポリエチレンケーブル", "spec": "CVT22sq", "qty_per_sqm": 0.15, "unit": "m", "category": "幹線設備"},  # 実績: 12m/82㎡
            {"name": "600V 架橋ポリエチレンケーブル", "spec": "CVT14sq", "qty_per_sqm": 0.28, "unit": "m", "category": "幹線設備"},  # 実績: 23m/82㎡
            {"name": "波付硬質ポリエチレン管", "spec": "FEP30", "qty_per_sqm": 0.15, "unit": "m", "category": "幹線設備"},  # 実績: 12m/82㎡
            {"name": "波付硬質ポリエチレン管", "spec": "FEP50", "qty_per_sqm": 0.15, "unit": "m", "category": "幹線設備"},  # 実績: 12m/82㎡

            # 受変電設備
            {"name": "電力量計", "spec": "電灯 120A", "qty_fixed": 1, "unit": "ヶ所", "category": "受変電設備"},
            {"name": "電力量計", "spec": "動力 60A", "qty_fixed": 1, "unit": "ヶ所", "category": "受変電設備"},

            # 分電盤
            {"name": "電灯盤", "spec": "(M)60A 20A×20 リース品", "qty_fixed": 1, "unit": "面", "category": "分電盤"},

            # 配線設備（人間見積実績ベース）
            {"name": "600V ビニルシースケーブル", "spec": "VVF2.0mm-2C", "qty_per_sqm": 0.40, "unit": "m", "category": "配線設備"},  # 実績: 33m/82㎡
            {"name": "600V ビニルシースケーブル", "spec": "VVF1.6mm-2C", "qty_per_sqm": 0.22, "unit": "m", "category": "配線設備"},  # 実績: 18m/82㎡
            {"name": "600V 架橋ポリエチレンケーブル", "spec": "CV5.5sq-3C", "qty_per_sqm": 0.27, "unit": "m", "category": "配線設備"},  # 実績: 22m/82㎡
            {"name": "600V 架橋ポリエチレンケーブル", "spec": "CV3sq-3C", "qty_per_sqm": 0.27, "unit": "m", "category": "配線設備"},  # 実績: 22m/82㎡

            # コンセント・スイッチ（人間見積実績ベース）
            {"name": "コンセント", "spec": "2P15A EETモダン", "qty_per_sqm": 0.05, "unit": "ヶ所", "category": "コンセント設備"},  # 実績: 約4個/82㎡
            {"name": "スイッチボックス", "spec": "1ヶ用", "qty_per_sqm": 0.23, "unit": "ヶ所", "category": "スイッチ設備"},  # 実績: 19個/82㎡

            # 照明設備
            {"name": "照明器具", "spec": "LED一体型 FL40W相当", "qty_per_sqm": 0.04, "unit": "台", "category": "照明設備"},  # 実績: 3台/82㎡
            {"name": "照明器具", "spec": "FL20W相当ウォールライト", "qty_fixed": 2, "unit": "台", "category": "照明設備"},

            # 諸経費
            {"name": "運搬搬入費", "spec": "", "qty_fixed": 1, "unit": "式", "category": "諸経費"},
            {"name": "消耗品雑材", "spec": "", "qty_fixed": 1, "unit": "式", "category": "諸経費"},
            {"name": "諸経費", "spec": "", "qty_fixed": 1, "unit": "式", "category": "諸経費"},
        ],

        "plumbing": [
            # 給水設備（人間見積実績ベース）
            {"name": "硬質塩化ビニル管", "spec": "給水 VP 屋内一式", "qty_per_sqm": 0.04, "unit": "m", "category": "給水設備"},  # 実績ベース
            {"name": "水道用耐衝撃性硬質塩ビ管", "spec": "給水 HIVP 屋外 20A", "qty_per_sqm": 0.24, "unit": "m", "category": "給水設備"},  # 実績: 20m/82㎡
            {"name": "給水バルブ", "spec": "20mm", "qty_fixed": 2, "unit": "ヶ所", "category": "給水設備"},

            # 排水設備（人間見積実績ベース）
            {"name": "硬質塩化ビニル管", "spec": "排水・通気 VU 屋内 50A", "qty_per_sqm": 0.04, "unit": "m", "category": "排水設備"},  # 実績: 3m/82㎡
            {"name": "硬質塩化ビニル管", "spec": "排水・通気 VU 屋外 50A", "qty_per_sqm": 0.04, "unit": "m", "category": "排水設備"},  # 実績: 3m/82㎡
            {"name": "硬質塩化ビニル管", "spec": "排水・通気 VU 屋外 100A", "qty_per_sqm": 0.24, "unit": "m", "category": "排水設備"},  # 実績: 20m/82㎡

            # 衛生器具
            {"name": "流し台接続費", "spec": "", "qty_fixed": 1, "unit": "ヶ所", "category": "衛生器具"},
            {"name": "自在水栓", "spec": "13φ リース品", "qty_fixed": 2, "unit": "個", "category": "衛生器具"},
            {"name": "混合水栓", "spec": "シングルレバー 直接飲用不可", "qty_fixed": 1, "unit": "個", "category": "衛生器具"},

            # 諸経費
            {"name": "運搬搬入費", "spec": "", "qty_fixed": 1, "unit": "式", "category": "諸経費"},
            {"name": "諸経費", "spec": "", "qty_fixed": 1, "unit": "式", "category": "諸経費"},
        ],

        "mechanical": [
            # 空調設備（仮設事務所は通常リース品のため項目少なめ）
            {"name": "エフモール", "spec": "2号", "qty_per_sqm": 0.60, "unit": "m", "category": "空調設備"},  # 実績: 49m/82㎡
            {"name": "保温工事", "spec": "屋外露出 簡易ウレタン保温", "qty_per_sqm": 0.24, "unit": "m", "category": "空調設備"},  # 実績: 20m/82㎡

            # 諸経費
            {"name": "運搬搬入費", "spec": "", "qty_fixed": 1, "unit": "式", "category": "諸経費"},
            {"name": "諸経費", "spec": "", "qty_fixed": 1, "unit": "式", "category": "諸経費"},
        ],
    },

    "学校": {
        "electrical": [
            # 受変電設備
            {"name": "キュービクル", "spec": "屋外型 300kVA", "qty_fixed": 1, "unit": "台", "category": "受変電設備"},
            {"name": "高圧引込ケーブル", "spec": "CV 38sq-3C", "qty_fixed": 50, "unit": "m", "category": "受変電設備"},
            {"name": "高圧気中負荷開閉器", "spec": "PAS", "qty_fixed": 1, "unit": "台", "category": "受変電設備"},

            # 幹線設備
            {"name": "600V CVケーブル", "spec": "CV 38sq-3C", "qty_per_sqm": 0.05, "unit": "m", "category": "幹線設備"},
            {"name": "600V CVケーブル", "spec": "CV 22sq-3C", "qty_per_sqm": 0.08, "unit": "m", "category": "幹線設備"},
            {"name": "600V CVケーブル", "spec": "CV 14sq-3C", "qty_per_sqm": 0.1, "unit": "m", "category": "幹線設備"},
            {"name": "ケーブルラック", "spec": "W300", "qty_per_sqm": 0.03, "unit": "m", "category": "幹線設備"},

            # 分電盤
            {"name": "分電盤", "spec": "電灯用 主幹100A", "qty_per_floor": 2, "unit": "面", "category": "分電盤"},
            {"name": "分電盤", "spec": "動力用 主幹60A", "qty_per_floor": 1, "unit": "面", "category": "分電盤"},

            # 配線設備
            {"name": "600V VVFケーブル", "spec": "2.0mm-2C", "qty_per_sqm": 0.8, "unit": "m", "category": "配線設備"},
            {"name": "600V VVFケーブル", "spec": "1.6mm-2C", "qty_per_sqm": 0.5, "unit": "m", "category": "配線設備"},

            # 照明設備
            {"name": "LED照明器具", "spec": "40W×2灯相当", "qty_per_sqm": 0.03, "unit": "台", "category": "照明設備"},
            {"name": "非常照明", "spec": "LED 電池内蔵型", "qty_per_sqm": 0.005, "unit": "台", "category": "照明設備"},
            {"name": "誘導灯", "spec": "B級 両面", "qty_per_sqm": 0.002, "unit": "台", "category": "照明設備"},

            # コンセント
            {"name": "コンセント", "spec": "2P15A×2 EET", "qty_per_sqm": 0.05, "unit": "ヶ所", "category": "コンセント設備"},

            # 弱電設備
            {"name": "LAN配線", "spec": "Cat6", "qty_per_sqm": 0.03, "unit": "ヶ所", "category": "弱電設備"},
            {"name": "電話配線", "spec": "", "qty_per_sqm": 0.02, "unit": "ヶ所", "category": "弱電設備"},
            {"name": "放送設備", "spec": "スピーカー", "qty_per_sqm": 0.01, "unit": "台", "category": "弱電設備"},

            # 防災設備
            {"name": "自動火災報知設備", "spec": "感知器", "qty_per_sqm": 0.02, "unit": "個", "category": "防災設備"},
            {"name": "非常放送設備", "spec": "スピーカー", "qty_per_sqm": 0.01, "unit": "台", "category": "防災設備"},
        ],

        "plumbing": [
            # 給水設備
            {"name": "給水管", "spec": "HIVP 25A", "qty_per_sqm": 0.1, "unit": "m", "category": "給水設備"},
            {"name": "給水管", "spec": "HIVP 20A", "qty_per_sqm": 0.15, "unit": "m", "category": "給水設備"},
            {"name": "給水管", "spec": "HIVP 13A", "qty_per_sqm": 0.2, "unit": "m", "category": "給水設備"},

            # 排水設備
            {"name": "排水管", "spec": "VU 100A", "qty_per_sqm": 0.08, "unit": "m", "category": "排水設備"},
            {"name": "排水管", "spec": "VU 75A", "qty_per_sqm": 0.1, "unit": "m", "category": "排水設備"},
            {"name": "排水管", "spec": "VU 50A", "qty_per_sqm": 0.15, "unit": "m", "category": "排水設備"},

            # 衛生器具
            {"name": "大便器", "spec": "洋風 ロータンク式", "qty_per_sqm": 0.005, "unit": "台", "category": "衛生器具"},
            {"name": "小便器", "spec": "壁掛式", "qty_per_sqm": 0.003, "unit": "台", "category": "衛生器具"},
            {"name": "洗面器", "spec": "壁掛式", "qty_per_sqm": 0.005, "unit": "台", "category": "衛生器具"},
        ],

        "mechanical": [
            # 空調設備
            {"name": "パッケージエアコン", "spec": "天井カセット型 5HP", "qty_per_sqm": 0.005, "unit": "台", "category": "空調設備"},
            {"name": "パッケージエアコン", "spec": "天井カセット型 3HP", "qty_per_sqm": 0.008, "unit": "台", "category": "空調設備"},
            {"name": "冷媒配管", "spec": "", "qty_per_sqm": 0.15, "unit": "m", "category": "空調設備"},
            {"name": "ドレン配管", "spec": "VP25", "qty_per_sqm": 0.1, "unit": "m", "category": "空調設備"},

            # 換気設備
            {"name": "全熱交換器", "spec": "天井埋込型", "qty_per_sqm": 0.002, "unit": "台", "category": "換気設備"},
            {"name": "換気扇", "spec": "天井埋込型", "qty_per_sqm": 0.01, "unit": "台", "category": "換気設備"},
        ],
    },
}


def detect_building_type(spec_text: str) -> str:
    """
    仕様書テキストから建物タイプを自動判定

    Args:
        spec_text: 仕様書のテキスト

    Returns:
        建物タイプ名（"仮設事務所", "学校", etc.）
    """
    keywords = {
        "仮設事務所": ["仮設", "事務所", "定検", "プレハブ", "ユニットハウス", "現場事務所", "仮設建物"],
        "学校": ["学校", "高校", "中学", "小学", "校舎", "体育館", "教室"],
        "工場": ["工場", "発電所", "プラント", "製造", "倉庫"],
        "オフィス": ["オフィス", "事務所ビル", "ビル"],
    }

    spec_lower = spec_text.lower()

    for btype, kws in keywords.items():
        if any(kw in spec_text for kw in kws):
            return btype

    return "仮設事務所"  # Default


def calculate_quantity(
    item_def: Dict[str, Any],
    floor_area: float,
    num_floors: int = 1
) -> float:
    """
    テンプレート定義から数量を計算

    Args:
        item_def: 項目定義（qty_per_sqm, qty_fixed, qty_per_floor等）
        floor_area: 延床面積（㎡）
        num_floors: 階数

    Returns:
        計算された数量
    """
    if "qty_per_sqm" in item_def:
        return floor_area * item_def["qty_per_sqm"]
    elif "qty_per_floor" in item_def:
        return num_floors * item_def["qty_per_floor"]
    elif "qty_fixed" in item_def:
        return item_def["qty_fixed"]
    else:
        return 1


def get_template_items(
    building_type: str,
    discipline: str,
    floor_area: float,
    num_floors: int = 1
) -> List[Dict[str, Any]]:
    """
    テンプレートから詳細項目リストを生成

    Args:
        building_type: 建物タイプ
        discipline: 工事区分（"electrical", "plumbing", "mechanical"）
        floor_area: 延床面積（㎡）
        num_floors: 階数

    Returns:
        項目リスト（name, spec, quantity, unit, category, qty_basis）
    """
    template = BUILDING_TEMPLATES.get(building_type, BUILDING_TEMPLATES.get("仮設事務所", {}))
    items_def = template.get(discipline, [])

    items = []
    for item_def in items_def:
        qty = calculate_quantity(item_def, floor_area, num_floors)

        # 数量算出根拠を生成
        if "qty_per_sqm" in item_def:
            qty_basis = f"床面積{floor_area}㎡ × {item_def['qty_per_sqm']}/㎡ = {qty}"
        elif "qty_per_floor" in item_def:
            qty_basis = f"階数{num_floors}階 × {item_def['qty_per_floor']}/階 = {qty}"
        elif "qty_fixed" in item_def:
            qty_basis = f"固定数量: {item_def['qty_fixed']}"
        else:
            qty_basis = "標準数量: 1"

        # Round to reasonable values
        if item_def.get("unit") == "m":
            qty = round(qty)  # Round meters to whole numbers
        elif item_def.get("unit") in ["台", "面", "ヶ所", "個"]:
            qty = max(1, round(qty))  # At least 1 for countable items
        else:
            qty = round(qty, 1)

        if qty > 0:
            items.append({
                "name": item_def["name"],
                "specification": item_def.get("spec", ""),
                "quantity": qty,
                "unit": item_def.get("unit", "式"),
                "category": item_def.get("category", ""),
                "qty_basis": qty_basis,
            })

    return items
