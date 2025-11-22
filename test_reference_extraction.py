"""
参照見積書からの項目抽出をテストするスクリプト
"""

import logging
from pathlib import Path
from pipelines.estimate_from_reference import EstimateFromReference
from pipelines.schemas import DisciplineType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_electrical_mechanical_extraction():
    """電気・機械設備の参照見積書からの抽出をテスト"""

    # ファイルパス
    spec_pdf = "test-files/仕様書【都立山崎高等学校仮設校舎等の借入れ】ord202403101060100130187c1e4d0.pdf"
    reference_pdf = "test-files/250723_送付状　見積書（電気・機械）.pdf"

    if not Path(spec_pdf).exists():
        print(f"❌ 仕様書が見つかりません: {spec_pdf}")
        return

    if not Path(reference_pdf).exists():
        print(f"❌ 参照見積書が見つかりません: {reference_pdf}")
        return

    print("=" * 80)
    print("電気・機械設備 - 参照見積書からの抽出テスト")
    print("=" * 80)

    generator = EstimateFromReference()

    # 電気設備の抽出
    print("\n【電気設備の抽出】")
    print(f"参照見積書: {Path(reference_pdf).name} ({Path(reference_pdf).stat().st_size / 1024 / 1024:.1f}MB)")

    fmt_doc_electrical = generator.generate_estimate_from_reference(
        spec_pdf,
        reference_pdf,
        DisciplineType.ELECTRICAL
    )

    print(f"\n抽出結果:")
    print(f"  総項目数: {len(fmt_doc_electrical.estimate_items)}項目")

    # 工事区分別の項目数
    by_discipline = {}
    for item in fmt_doc_electrical.estimate_items:
        discipline = item.discipline or DisciplineType.ELECTRICAL
        if discipline not in by_discipline:
            by_discipline[discipline] = []
        by_discipline[discipline].append(item)

    for discipline, items in by_discipline.items():
        print(f"    {discipline.value}: {len(items)}項目")

    # 金額情報
    total = sum(item.amount or 0 for item in fmt_doc_electrical.estimate_items)
    with_price = sum(1 for item in fmt_doc_electrical.estimate_items if item.unit_price)
    print(f"  単価付与: {with_price}/{len(fmt_doc_electrical.estimate_items)}項目 ({with_price/len(fmt_doc_electrical.estimate_items)*100:.1f}%)")
    print(f"  推定総額: ¥{total:,.0f}")

    # サンプル項目を表示（最初の10項目）
    print(f"\n【サンプル項目（最初の10項目）】")
    for i, item in enumerate(fmt_doc_electrical.estimate_items[:10], 1):
        print(f"  {i}. {item.name} - {item.specification or '（仕様なし）'}")
        print(f"     数量: {item.quantity} {item.unit}, 単価: ¥{item.unit_price:,.0f}, 金額: ¥{item.amount:,.0f}")

    # 機械設備の抽出
    print("\n" + "=" * 80)
    print("【機械設備の抽出】")
    print(f"参照見積書: {Path(reference_pdf).name} (同じファイル)")

    fmt_doc_mechanical = generator.generate_estimate_from_reference(
        spec_pdf,
        reference_pdf,
        DisciplineType.MECHANICAL
    )

    print(f"\n抽出結果:")
    print(f"  総項目数: {len(fmt_doc_mechanical.estimate_items)}項目")

    # 工事区分別の項目数
    by_discipline = {}
    for item in fmt_doc_mechanical.estimate_items:
        discipline = item.discipline or DisciplineType.MECHANICAL
        if discipline not in by_discipline:
            by_discipline[discipline] = []
        by_discipline[discipline].append(item)

    for discipline, items in by_discipline.items():
        print(f"    {discipline.value}: {len(items)}項目")

    # 金額情報
    total = sum(item.amount or 0 for item in fmt_doc_mechanical.estimate_items)
    with_price = sum(1 for item in fmt_doc_mechanical.estimate_items if item.unit_price)
    print(f"  単価付与: {with_price}/{len(fmt_doc_mechanical.estimate_items)}項目 ({with_price/len(fmt_doc_mechanical.estimate_items)*100:.1f}%)")
    print(f"  推定総額: ¥{total:,.0f}")

    # サンプル項目を表示（最初の10項目）
    print(f"\n【サンプル項目（最初の10項目）】")
    for i, item in enumerate(fmt_doc_mechanical.estimate_items[:10], 1):
        print(f"  {i}. {item.name} - {item.specification or '（仕様なし）'}")
        print(f"     数量: {item.quantity} {item.unit}, 単価: ¥{item.unit_price:,.0f}, 金額: ¥{item.amount:,.0f}")

    print("\n" + "=" * 80)
    print("✅ テスト完了")


if __name__ == "__main__":
    test_electrical_mechanical_extraction()
