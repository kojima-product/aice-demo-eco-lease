#!/usr/bin/env python3
"""
精度向上テスト

Phase 1: Vision抽出による諸元表データ取得
Phase 2: KBマッチングロジック改善
"""

import sys
sys.path.insert(0, '.')

from pathlib import Path
from pipelines.estimate_generator_ai import AIEstimateGenerator
from pipelines.schemas import DisciplineType
import json

def test_accuracy_improvements():
    """精度向上テスト"""
    print("=" * 80)
    print("精度向上テスト")
    print("=" * 80)

    spec_path = "test-files/仕様書【都立山崎高等学校仮設校舎等の借入れ】ord202403101060100130187c1e4d0.pdf"

    if not Path(spec_path).exists():
        print(f"❌ ファイルが見つかりません: {spec_path}")
        return

    generator = AIEstimateGenerator()

    print("\n【Phase 1: Vision抽出テスト】")
    print("-" * 40)

    # Vision抽出テスト
    vision_data = generator.extract_specification_table_with_vision(spec_path, target_pages=[39, 40])

    print(f"  部屋タイプ数: {len(vision_data.get('rooms', []))}")
    print(f"  総部屋数: {vision_data.get('totals', {}).get('room_count', 0)}")
    print(f"  ガス栓総数: {vision_data.get('totals', {}).get('gas_outlet_total', 0)}")
    print(f"  コンセント総数: {vision_data.get('totals', {}).get('electrical_outlet_total', 0)}")

    if vision_data.get("rooms"):
        print("\n  【抽出された部屋データ（最初の5件）】")
        for room in vision_data["rooms"][:5]:
            gas = room.get("gas_outlets", 0)
            elec = room.get("electrical_outlets", 0)
            count = room.get("count", 1)
            print(f"    - {room.get('room_name', '不明')}: {count}室, ガス栓={gas}, コンセント={elec}")

    print("\n【Phase 2: KBマッチングテスト（電気設備）】")
    print("-" * 40)

    # 電気設備見積生成
    fmt_doc = generator.generate_estimate(spec_path, DisciplineType.ELECTRICAL)

    print(f"  生成項目数: {len(fmt_doc.estimate_items)}")

    # 単価マッチング統計
    items_with_price = [item for item in fmt_doc.estimate_items if item.unit_price is not None]
    items_without_price = [item for item in fmt_doc.estimate_items if item.unit_price is None and item.level > 0 and item.quantity]

    match_rate = len(items_with_price) / len(fmt_doc.estimate_items) * 100 if fmt_doc.estimate_items else 0
    print(f"  単価付与率: {len(items_with_price)}/{len(fmt_doc.estimate_items)} ({match_rate:.1f}%)")

    # 合計金額
    total = sum(item.amount or 0 for item in fmt_doc.estimate_items if item.level == 0)
    print(f"  推定総額: ¥{total:,.0f}")

    # 単価未付与の項目（最初の10件）
    if items_without_price:
        print(f"\n  【単価未付与の項目（{len(items_without_price)}件中最初の10件）】")
        for item in items_without_price[:10]:
            print(f"    - {item.name} {item.specification or ''}")

    # 結果を保存
    output_path = Path("output/accuracy_test_result.json")
    output_path.parent.mkdir(exist_ok=True)

    result = {
        "vision_extraction": {
            "room_types": len(vision_data.get("rooms", [])),
            "total_rooms": vision_data.get("totals", {}).get("room_count", 0),
            "gas_outlets": vision_data.get("totals", {}).get("gas_outlet_total", 0),
            "electrical_outlets": vision_data.get("totals", {}).get("electrical_outlet_total", 0)
        },
        "estimate_generation": {
            "item_count": len(fmt_doc.estimate_items),
            "items_with_price": len(items_with_price),
            "match_rate": match_rate,
            "total_amount": total
        }
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n結果を保存しました: {output_path}")
    print("=" * 80)

    return result


if __name__ == "__main__":
    test_accuracy_improvements()
