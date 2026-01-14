"""
見積項目のカテゴリ別階層構造モジュール

見積項目を適切なカテゴリに分類し、階層構造を付与します。
"""

from typing import List, Dict, Any
from loguru import logger
from pipelines.schemas import EstimateItem, DisciplineType


# カテゴリ定義（工事区分 → カテゴリ → キーワード）
CATEGORY_DEFINITIONS = {
    DisciplineType.ELECTRICAL: {
        "配線工事": {
            "keywords": ["ケーブル", "電線", "CV", "VVF", "CVT", "配線", "幹線"],
            "order": 1
        },
        "配管工事": {
            "keywords": ["電線管", "PF管", "FEP", "配管", "ダクト"],
            "order": 2
        },
        "器具工事": {
            "keywords": ["コンセント", "スイッチ", "照明", "LED", "器具"],
            "order": 3
        },
        "盤工事": {
            "keywords": ["分電盤", "電灯盤", "動力盤", "キュービクル", "電力量計"],
            "order": 4
        },
        "弱電工事": {
            "keywords": ["LAN", "電話", "インターホン", "放送", "弱電"],
            "order": 5
        },
        "その他": {
            "keywords": [],
            "order": 99
        }
    },
    DisciplineType.PLUMBING: {
        "給水工事": {
            "keywords": ["給水", "HIVP", "水栓", "量水器", "給水管"],
            "order": 1
        },
        "排水工事": {
            "keywords": ["排水", "VU", "排水管", "通気", "トラップ"],
            "order": 2
        },
        "給湯工事": {
            "keywords": ["給湯", "温水器", "湯沸器", "給湯管"],
            "order": 3
        },
        "衛生器具工事": {
            "keywords": ["便器", "洗面", "流し", "シンク", "器具"],
            "order": 4
        },
        "その他": {
            "keywords": [],
            "order": 99
        }
    },
    DisciplineType.MECHANICAL: {
        "空調機器工事": {
            "keywords": ["エアコン", "空調機", "室内機", "室外機"],
            "order": 1
        },
        "換気工事": {
            "keywords": ["換気", "ファン", "ダクト", "吹出口", "吸込口"],
            "order": 2
        },
        "配管工事": {
            "keywords": ["冷媒管", "ドレン", "配管"],
            "order": 3
        },
        "その他": {
            "keywords": [],
            "order": 99
        }
    },
    DisciplineType.GAS: {
        "配管工事": {
            "keywords": ["ガス管", "白ガス管", "PE管", "配管"],
            "order": 1
        },
        "器具工事": {
            "keywords": ["ガス栓", "ガスコンセント", "ネジコック"],
            "order": 2
        },
        "その他": {
            "keywords": [],
            "order": 99
        }
    }
}


def categorize_item(item: EstimateItem, discipline: DisciplineType) -> str:
    """
    見積項目をカテゴリに分類

    Args:
        item: 見積項目
        discipline: 工事区分

    Returns:
        カテゴリ名
    """
    if discipline not in CATEGORY_DEFINITIONS:
        return "その他"

    categories = CATEGORY_DEFINITIONS[discipline]
    item_name = item.name or ""
    item_spec = item.specification or ""
    combined = f"{item_name} {item_spec}".upper()

    for category_name, category_def in categories.items():
        if category_name == "その他":
            continue

        for keyword in category_def.get("keywords", []):
            if keyword.upper() in combined:
                return category_name

    return "その他"


def get_category_order(category: str, discipline: DisciplineType) -> int:
    """
    カテゴリの表示順序を取得
    """
    if discipline not in CATEGORY_DEFINITIONS:
        return 99

    categories = CATEGORY_DEFINITIONS[discipline]
    if category in categories:
        return categories[category].get("order", 99)
    return 99


def organize_items_by_category(
    items: List[EstimateItem],
    discipline: DisciplineType
) -> List[EstimateItem]:
    """
    見積項目をカテゴリ別に整理し、階層構造を付与

    Args:
        items: 見積項目リスト（親項目level=0以外）
        discipline: 工事区分

    Returns:
        階層構造を付与された項目リスト
    """
    if not items:
        return []

    # カテゴリ別にグループ化
    categorized: Dict[str, List[EstimateItem]] = {}
    parent_item = None

    for item in items:
        if item.level == 0:
            parent_item = item
            continue

        category = categorize_item(item, discipline)
        if category not in categorized:
            categorized[category] = []
        categorized[category].append(item)

    # カテゴリ順でソート
    sorted_categories = sorted(
        categorized.keys(),
        key=lambda c: get_category_order(c, discipline)
    )

    # 階層構造を構築
    organized_items = []

    # 親項目（level 0）を追加
    if parent_item:
        organized_items.append(parent_item)

    for category in sorted_categories:
        category_items = categorized[category]
        if not category_items:
            continue

        # カテゴリ親項目（level 1）を作成
        category_parent = EstimateItem(
            item_no=f"C{len(organized_items):03d}",
            name=category,
            specification="",
            quantity=1,
            unit="式",
            level=1,
            discipline=discipline,
            confidence=1.0,
            source_type="category",
            source_reference="CATEGORY_GROUPING",
            estimation_basis="カテゴリ分類",
        )
        organized_items.append(category_parent)

        # カテゴリ内の項目（level 2）を追加
        for item in category_items:
            item.level = 2
            item.parent_item_no = category
            organized_items.append(item)

    logger.info(f"Organized {len(items)} items into {len(sorted_categories)} categories for {discipline.value}")

    return organized_items


def add_category_hierarchy(
    items: List[EstimateItem],
    discipline: DisciplineType
) -> List[EstimateItem]:
    """
    既存の項目リストにカテゴリ階層を追加

    level=0 の親項目はそのまま維持し、
    level=1 の項目をカテゴリに分類してlevel=2に変更します。

    Args:
        items: 見積項目リスト
        discipline: 工事区分

    Returns:
        カテゴリ階層が追加された項目リスト
    """
    if not items:
        return []

    # 親項目（level=0）と子項目を分離
    parent_item = None
    child_items = []

    for item in items:
        if item.level == 0:
            parent_item = item
        else:
            child_items.append(item)

    if not child_items:
        return items

    # カテゴリ別にグループ化
    categorized: Dict[str, List[EstimateItem]] = {}

    for item in child_items:
        category = categorize_item(item, discipline)
        if category not in categorized:
            categorized[category] = []
        categorized[category].append(item)

    # カテゴリ順でソート
    sorted_categories = sorted(
        categorized.keys(),
        key=lambda c: get_category_order(c, discipline)
    )

    # 階層構造を構築
    organized_items = []

    # 親項目（level 0）を追加
    if parent_item:
        organized_items.append(parent_item)

    item_counter = 1
    for category in sorted_categories:
        category_items = categorized[category]
        if not category_items:
            continue

        # カテゴリが1つだけの場合は中間階層を省略
        if len(sorted_categories) == 1:
            for item in category_items:
                item.level = 1
                organized_items.append(item)
            continue

        # カテゴリ親項目（level 1）を作成
        category_total = sum(
            (item.amount or 0) for item in category_items
        )
        category_parent = EstimateItem(
            item_no=f"C{item_counter:03d}",
            name=category,
            specification="",
            quantity=1,
            unit="式",
            level=1,
            discipline=discipline,
            amount=category_total,
            confidence=1.0,
            source_type="category",
            source_reference="CATEGORY_GROUPING",
            estimation_basis="カテゴリ小計",
        )
        organized_items.append(category_parent)
        item_counter += 1

        # カテゴリ内の項目（level 2）を追加
        for item in category_items:
            item.level = 2
            item.parent_item_no = category
            organized_items.append(item)

    logger.info(f"Added category hierarchy: {len(child_items)} items -> {len(sorted_categories)} categories for {discipline.value}")

    return organized_items


if __name__ == "__main__":
    # Test
    from pipelines.schemas import EstimateItem, DisciplineType

    test_items = [
        EstimateItem(item_no="1", name="電気設備工事", level=0, discipline=DisciplineType.ELECTRICAL, unit="式"),
        EstimateItem(item_no="2", name="600V CVケーブル", specification="CV8sq-3C", level=1, discipline=DisciplineType.ELECTRICAL, unit="m"),
        EstimateItem(item_no="3", name="VVFケーブル", specification="2.0mm-2C", level=1, discipline=DisciplineType.ELECTRICAL, unit="m"),
        EstimateItem(item_no="4", name="コンセント", specification="2P15A", level=1, discipline=DisciplineType.ELECTRICAL, unit="箇所"),
        EstimateItem(item_no="5", name="LED照明器具", level=1, discipline=DisciplineType.ELECTRICAL, unit="台"),
        EstimateItem(item_no="6", name="分電盤", specification="60A", level=1, discipline=DisciplineType.ELECTRICAL, unit="面"),
    ]

    organized = add_category_hierarchy(test_items, DisciplineType.ELECTRICAL)

    for item in organized:
        indent = "  " * item.level
        print(f"{indent}{item.name} (level={item.level})")
