#!/usr/bin/env python3
"""
PDF見積書からKBを構築するバッチスクリプト

使用方法:
    python scripts/build_kb_from_pdfs.py

処理内容:
    1. data/フォルダ内の全PDF見積書を検索
    2. OCRで項目・単価を抽出
    3. 既存KBとマージ
    4. kb/price_kb.jsonに保存
"""

import sys
from pathlib import Path
import json
from datetime import datetime

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from pipelines.kb_builder import PriceKBBuilder
from loguru import logger


def find_pdf_estimates():
    """送付状見積書PDFを検索"""
    data_dir = project_root / "data"
    pdf_files = []

    for pdf_path in data_dir.rglob("*送付状*見積書*.pdf"):
        # 考察ファイルは除外
        if "考察" in pdf_path.name:
            continue
        pdf_files.append(pdf_path)

    return sorted(pdf_files)


def extract_project_name(pdf_path: Path) -> str:
    """パスからプロジェクト名を抽出"""
    # data/XXX_プロジェクト名/④見積書/... の形式を想定
    parts = pdf_path.parts
    for i, part in enumerate(parts):
        if part == "data" and i + 1 < len(parts):
            return parts[i + 1]
    return pdf_path.stem


def main():
    print("=" * 60)
    print("PDF見積書からKB構築")
    print("=" * 60)

    # PDF見積書を検索
    pdf_files = find_pdf_estimates()
    print(f"\n見つかったPDF見積書: {len(pdf_files)}件")

    if not pdf_files:
        print("PDF見積書が見つかりませんでした")
        return

    # KB Builderを初期化
    kb = PriceKBBuilder()

    # 既存KBを読み込み
    kb_path = project_root / "kb" / "price_kb.json"
    existing_refs = kb.load_kb_from_json(str(kb_path))
    print(f"既存KB項目数: {len(existing_refs)}")

    # 各PDFから抽出
    all_extracted = []
    success_count = 0
    error_count = 0

    for i, pdf_path in enumerate(pdf_files, 1):
        project_name = extract_project_name(pdf_path)
        print(f"\n[{i}/{len(pdf_files)}] {project_name}")
        print(f"  ファイル: {pdf_path.name}")

        try:
            refs = kb.extract_estimate_from_pdf(
                str(pdf_path),
                project_name=project_name
            )
            print(f"  抽出項目数: {len(refs)}")
            all_extracted.extend(refs)
            success_count += 1
        except Exception as e:
            print(f"  エラー: {e}")
            error_count += 1
            continue

    print(f"\n抽出完了: {success_count}件成功, {error_count}件エラー")
    print(f"抽出項目総数: {len(all_extracted)}")

    if not all_extracted:
        print("抽出された項目がありません")
        return

    # 複数見積を統合（中央値）
    print("\n複数見積を統合中（中央値）...")
    aggregated = kb.aggregate_multiple_estimates(
        all_extracted,
        method="median"
    )
    print(f"統合後項目数: {len(aggregated)}")

    # 既存KBとマージ
    print("\n既存KBとマージ中...")
    merged = kb.merge_with_existing_kb(
        aggregated,
        merge_strategy="keep_new"
    )
    print(f"マージ後項目数: {len(merged)}")

    # バックアップを作成
    backup_path = kb_path.with_suffix(f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    if kb_path.exists():
        with open(kb_path, 'r', encoding='utf-8') as f:
            backup_data = f.read()
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(backup_data)
        print(f"\nバックアップ作成: {backup_path.name}")

    # 保存
    kb.save_kb_to_json(merged, str(kb_path))
    print(f"KB保存完了: {kb_path}")

    # 統計を表示
    print("\n" + "=" * 60)
    print("KB統計")
    print("=" * 60)

    disciplines = {}
    for ref in merged:
        d = ref.discipline if hasattr(ref, 'discipline') else ref.get('discipline', '不明')
        disciplines[d] = disciplines.get(d, 0) + 1

    for d, count in sorted(disciplines.items(), key=lambda x: -x[1]):
        print(f"  {d}: {count}件")


if __name__ == "__main__":
    main()
