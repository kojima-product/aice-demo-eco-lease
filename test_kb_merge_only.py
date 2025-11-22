"""KBマージ機能のみをテスト"""

from pipelines.kb_builder import PriceKBBuilder
from pipelines.schemas import PriceReference, DisciplineType
from datetime import date

# テスト用の新規データを手動で作成
new_refs = [
    PriceReference(
        item_id="TEST_001",
        description="テストアイテム1",
        discipline=DisciplineType.GAS,
        unit="個",
        unit_price=5000.0,
        vendor=None,
        valid_from=date.today(),
        valid_to=None,
        source_project="test_project",
        context_tags=["テスト"],
        features={"specification": "test_spec"},
        similarity_score=0.0
    ),
    PriceReference(
        item_id="TEST_002",
        description="白ガス管（ネジ接合）",  # 既存KBにある項目
        discipline=DisciplineType.GAS,
        unit="m",
        unit_price=10000.0,  # 既存とは異なる価格
        vendor=None,
        valid_from=date.today(),
        valid_to=None,
        source_project="test_project",
        context_tags=["テスト"],
        features={"specification": "15A"},
        similarity_score=0.0
    )
]

print("\n" + "=" * 80)
print("テスト: 既存KBとのマージ")
print("=" * 80)

kb_builder = PriceKBBuilder(kb_path="kb/price_kb.json")

# 既存KB件数
existing_count = len(kb_builder.kb_items)
print(f"\n既存KB: {existing_count}項目")

# 新規データ
print(f"新規データ: {len(new_refs)}項目")
for ref in new_refs:
    spec = ref.features.get("specification", "")
    print(f"  - {ref.description} {spec}: ¥{ref.unit_price:,}/{ref.unit}")

# マージ（新データを優先）
merged_refs = kb_builder.merge_with_existing_kb(
    new_refs,
    merge_strategy="keep_new"
)

print(f"\n✅ マージ完了: {len(merged_refs)}項目")
print(f"   追加された新規項目: {len(merged_refs) - existing_count}項目")

# マージ後の確認
print("\n【マージ後のテスト項目確認】")
test_items = [ref for ref in merged_refs if ref.item_id.startswith("TEST_")]
for ref in test_items:
    spec = ref.features.get("specification", "")
    print(f"  - {ref.description} {spec}: ¥{ref.unit_price:,}/{ref.unit}")

# テスト用KBに保存
kb_builder.save_kb_to_json(merged_refs, "kb/price_kb_merged_test.json")
print(f"\n💾 マージ結果を保存: kb/price_kb_merged_test.json")

print("\n🎉 マージ機能のテスト完了！")
