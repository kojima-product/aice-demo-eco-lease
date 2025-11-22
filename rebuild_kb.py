"""
KB完全再構築スクリプト

2つの見積書PDFから正しい工事区分でKBを構築します。
"""

import json
from pathlib import Path
from datetime import date
from pipelines.estimate_from_reference import EstimateFromReference
from pipelines.schemas import DisciplineType, PriceReference
from loguru import logger

def convert_estimate_items_to_kb(estimate_items, project_name: str):
    """EstimateItemをPriceReferenceに変換"""
    kb_items = []

    for i, item in enumerate(estimate_items):
        # 単価がある項目のみKB化
        if item.unit_price and item.unit_price > 0:
            # コンテキストタグの生成
            context_tags = []
            if "学校" in project_name or "高校" in project_name:
                context_tags.append("学校")
            if "改修" in project_name:
                context_tags.append("改修")
            if "仮設" in project_name:
                context_tags.append("仮設")

            # 工事区分の文字列を取得
            discipline_str = item.discipline.value if item.discipline else "不明"

            kb_item = {
                "item_id": f"{project_name}_{i+1:03d}",
                "description": item.name,
                "discipline": discipline_str,
                "unit": item.unit or "式",
                "unit_price": float(item.unit_price),
                "vendor": None,
                "valid_from": date.today().isoformat(),
                "valid_to": None,
                "source_project": project_name,
                "context_tags": context_tags,
                "features": {
                    "specification": item.specification or "",
                    "quantity": item.quantity,
                },
                "similarity_score": 0.0
            }
            kb_items.append(kb_item)

    return kb_items

def rebuild_kb():
    """KBを完全に再構築"""

    extractor = EstimateFromReference()

    # 1. 都市ガス見積書からKB化
    gas_pdf = "test-files/250918_送付状　見積書（都市ｶﾞｽ).pdf"

    all_kb_items = []

    if Path(gas_pdf).exists():
        print(f"\n{'='*80}")
        print(f"【都市ガス設備工事】KB構築")
        print(f"{'='*80}")
        print(f"ファイル: {gas_pdf} ({Path(gas_pdf).stat().st_size / 1024 / 1024:.1f}MB)")

        # ガス設備として抽出
        estimate_items = extractor.extract_estimate_from_pdf(
            pdf_path=gas_pdf,
            discipline=DisciplineType.GAS
        )

        print(f"\n抽出結果:")
        print(f"  抽出項目数: {len(estimate_items)}項目")

        # KB形式に変換
        kb_items_gas = convert_estimate_items_to_kb(estimate_items, "都立山崎高校_都市ガス")
        all_kb_items.extend(kb_items_gas)

        print(f"  KB登録数: {len(kb_items_gas)}項目（単価あり）")
    else:
        print(f"❌ ファイルが見つかりません: {gas_pdf}")
        return

    # 2. 電気・機械見積書は後でStreamlit UIからアップロード
    elec_mech_pdf = "test-files/250723_送付状　見積書（電気・機械）.pdf"

    print(f"\n{'='*80}")
    print(f"【電気・機械設備工事】")
    print(f"{'='*80}")
    print(f"⚠️ 電気・機械設備のPDFは119ページ（40MB）と大きいため、")
    print(f"   Streamlit UIの「KB管理」ページから手動でアップロードしてください。")
    print(f"   http://localhost:8503/ にアクセス → KB管理")

    print(f"\n✅ ガスKB構築完了")
    print(f"  総項目数: {len(all_kb_items)}項目")
    print(f"    - ガス設備: {len(kb_items_gas)}項目")

    # KBに保存
    with open("kb/price_kb.json", 'w', encoding='utf-8') as f:
        json.dump(all_kb_items, f, ensure_ascii=False, indent=2)

    print(f"\n  保存完了: kb/price_kb.json")

    # 3. KB内容を確認
    print(f"\n{'='*80}")
    print(f"【KB統計情報】")
    print(f"{'='*80}")

    with open("kb/price_kb.json", 'r', encoding='utf-8') as f:
        kb_data = json.load(f)

    # 工事区分別統計
    discipline_stats = {}
    for item in kb_data:
        disc = item.get("discipline", "不明")
        if disc not in discipline_stats:
            discipline_stats[disc] = []
        discipline_stats[disc].append(item)

    print(f"\n■ 工事区分別項目数:")
    for disc, items in sorted(discipline_stats.items()):
        print(f"  {disc}: {len(items)}項目")

        # サンプル表示（最初の3項目）
        print(f"    【サンプル】")
        for i, item in enumerate(items[:3], 1):
            desc = item.get("description", "")
            spec = item.get("features", {}).get("specification", "")
            unit_price = item.get("unit_price", 0)
            unit = item.get("unit", "")
            print(f"      {i}. {desc} {spec} - ¥{unit_price:,.0f}/{unit}")


if __name__ == "__main__":
    rebuild_kb()
