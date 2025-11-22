"""KB化機能のテストスクリプト

複数の見積書（Excel/PDF）からKBを構築し、統合・マージする機能をテストします。
"""

import os
from pathlib import Path
from pipelines.kb_builder import PriceKBBuilder

def test_excel_kb_extraction():
    """Excelファイルから単価KBを抽出するテスト"""
    print("\n" + "=" * 80)
    print("TEST 1: Excel見積書からのKB構築")
    print("=" * 80)

    kb_builder = PriceKBBuilder(kb_path="kb/price_kb_test.json")

    # 出力Excelファイルが既にある場合、それをKB化してみる
    excel_files = list(Path("output").glob("*.xlsx"))

    if not excel_files:
        print("❌ output/ディレクトリにExcelファイルが見つかりません")
        print("   先に見積書を生成してください: python test_template.py")
        return None

    excel_file = excel_files[0]
    print(f"\n📄 対象ファイル: {excel_file}")

    price_refs = kb_builder.extract_estimate_from_excel(
        str(excel_file),
        project_name="テスト案件_Excel"
    )

    if price_refs:
        print(f"\n✅ KB抽出完了: {len(price_refs)}項目")

        # サンプル表示
        print("\n【抽出サンプル（最初の5項目）】")
        for ref in price_refs[:5]:
            spec = ref.features.get("specification", "")
            spec_str = f" {spec}" if spec else ""
            print(f"  - {ref.description}{spec_str}: ¥{ref.unit_price:,}/{ref.unit}")

        return price_refs
    else:
        print("❌ KB抽出に失敗しました")
        return None


def test_pdf_kb_extraction():
    """PDF見積書からのKB構築テスト"""
    print("\n" + "=" * 80)
    print("TEST 2: PDF見積書からのKB構築")
    print("=" * 80)

    kb_builder = PriceKBBuilder(kb_path="kb/price_kb_test.json")

    # 既存の見積PDFを使用
    pdf_file = "test-files/250918_送付状　見積書（都市ｶﾞｽ).pdf"

    if not Path(pdf_file).exists():
        print(f"❌ PDFファイルが見つかりません: {pdf_file}")
        return None

    print(f"\n📄 対象ファイル: {pdf_file}")

    price_refs = kb_builder.extract_estimate_from_pdf(pdf_file)

    if price_refs:
        print(f"\n✅ KB抽出完了: {len(price_refs)}項目")

        # サンプル表示
        print("\n【抽出サンプル（最初の5項目）】")
        for ref in price_refs[:5]:
            spec = ref.features.get("specification", "")
            spec_str = f" {spec}" if spec else ""
            print(f"  - {ref.description}{spec_str}: ¥{ref.unit_price:,}/{ref.unit}")

        return price_refs
    else:
        print("❌ KB抽出に失敗しました")
        return None


def test_multi_estimate_aggregation():
    """複数見積の統合テスト"""
    print("\n" + "=" * 80)
    print("TEST 3: 複数見積の価格統合")
    print("=" * 80)

    kb_builder = PriceKBBuilder(kb_path="kb/price_kb_test.json")

    # 複数ファイルを指定（実際には同じファイルを2回使って統合ロジックをテスト）
    estimate_paths = []

    pdf_file = "test-files/250918_送付状　見積書（都市ｶﾞｽ).pdf"
    if Path(pdf_file).exists():
        estimate_paths.append(pdf_file)

    excel_files = list(Path("output").glob("*.xlsx"))
    if excel_files:
        estimate_paths.append(str(excel_files[0]))

    if len(estimate_paths) < 2:
        print("⚠️ 統合テストには最低2ファイル必要です")
        print(f"   現在: {len(estimate_paths)}ファイル")
        if estimate_paths:
            print(f"   デモとして1ファイルのみ処理します")
        else:
            print("❌ テスト対象ファイルがありません")
            return None

    print(f"\n📄 対象ファイル: {len(estimate_paths)}件")
    for path in estimate_paths:
        print(f"   - {Path(path).name}")

    # 中央値で統合
    aggregated_refs = kb_builder.aggregate_multiple_estimates(
        estimate_paths,
        method="median"
    )

    if aggregated_refs:
        print(f"\n✅ 統合完了: {len(aggregated_refs)}項目")

        # 統合情報を表示
        print("\n【統合結果サンプル（最初の5項目）】")
        for ref in aggregated_refs[:5]:
            spec = ref.features.get("specification", "")
            spec_str = f" {spec}" if spec else ""

            aggregated_from = ref.features.get("aggregated_from", 1)
            if aggregated_from > 1:
                price_range = ref.features.get("price_range", "")
                print(f"  - {ref.description}{spec_str}: ¥{ref.unit_price:,}/{ref.unit}")
                print(f"    (統合元: {aggregated_from}件, 価格レンジ: {price_range})")
            else:
                print(f"  - {ref.description}{spec_str}: ¥{ref.unit_price:,}/{ref.unit}")

        return aggregated_refs
    else:
        print("❌ 統合に失敗しました")
        return None


def test_kb_merge():
    """既存KBとのマージテスト"""
    print("\n" + "=" * 80)
    print("TEST 4: 既存KBとのマージ")
    print("=" * 80)

    kb_builder = PriceKBBuilder(kb_path="kb/price_kb.json")

    # 既存KBをロード
    existing_refs = kb_builder.load_kb_from_json("kb/price_kb.json")
    print(f"\n📦 既存KB: {len(existing_refs)}項目")

    # 新しいデータを抽出（PDFから）
    pdf_file = "test-files/250918_送付状　見積書（都市ｶﾞｽ).pdf"
    if not Path(pdf_file).exists():
        print(f"❌ PDFファイルが見つかりません: {pdf_file}")
        return None

    new_refs = kb_builder.extract_estimate_from_pdf(pdf_file)
    print(f"📦 新規データ: {len(new_refs)}項目")

    # マージ（新しいデータを優先）
    merged_refs = kb_builder.merge_with_existing_kb(
        new_refs,
        merge_strategy="keep_new"
    )

    print(f"\n✅ マージ完了: {len(merged_refs)}項目")

    # 統計情報
    added = len(merged_refs) - len(existing_refs)
    print(f"   追加項目: {added}件")
    print(f"   既存維持: {len(existing_refs) - added}件")

    # マージ結果を保存（テスト用）
    test_kb_path = "kb/price_kb_merged_test.json"
    kb_builder.save_kb_to_json(merged_refs, test_kb_path)
    print(f"\n💾 マージ結果を保存: {test_kb_path}")

    return merged_refs


if __name__ == "__main__":
    print("\n🧪 KB化機能テスト開始")
    print("=" * 80)

    # TEST 1: Excel抽出
    excel_refs = test_excel_kb_extraction()

    # TEST 2: PDF抽出
    pdf_refs = test_pdf_kb_extraction()

    # TEST 3: 複数見積統合
    aggregated_refs = test_multi_estimate_aggregation()

    # TEST 4: 既存KBとマージ
    merged_refs = test_kb_merge()

    # 総合結果
    print("\n" + "=" * 80)
    print("📊 テスト結果サマリー")
    print("=" * 80)

    test_results = [
        ("Excel抽出", excel_refs),
        ("PDF抽出", pdf_refs),
        ("複数見積統合", aggregated_refs),
        ("KBマージ", merged_refs)
    ]

    success_count = sum(1 for _, result in test_results if result is not None)

    for test_name, result in test_results:
        status = "✅ 成功" if result is not None else "❌ 失敗"
        count = f"({len(result)}項目)" if result else ""
        print(f"  {status} {test_name} {count}")

    print(f"\n総合: {success_count}/{len(test_results)} テスト成功")

    if success_count == len(test_results):
        print("\n🎉 全てのKB化機能が正常に動作しています！")
    else:
        print("\n⚠️ 一部のテストが失敗しました。詳細を確認してください。")
