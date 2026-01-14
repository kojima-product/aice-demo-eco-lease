#!/usr/bin/env python3
"""
人間見積PDFをKBに登録するスクリプト

大洲バイオマス発電所の見積書（電気+給排水）をKBに追加し、
類似案件での参照見積として使用できるようにする。
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from pipelines.kb_builder import PriceKBBuilder
from pipelines.schemas import DisciplineType
from loguru import logger


def main():
    # Human estimate PDF path
    estimate_pdf = Path("data/7木村‗大洲バイオマス発電所/④　見積書/250131_送付状　見積書.pdf")

    if not estimate_pdf.exists():
        logger.error(f"見積書が見つかりません: {estimate_pdf}")
        return

    logger.info(f"見積書をKBに登録: {estimate_pdf}")

    # Initialize KB builder
    kb_builder = PriceKBBuilder(kb_path="kb/price_kb.json")

    # Extract price references from human estimate PDF
    logger.info("見積書から単価情報を抽出中...")
    price_refs = kb_builder.extract_estimate_from_pdf(
        str(estimate_pdf),
        project_name="大洲バイオマス発電所_仮設事務所"
    )

    if not price_refs:
        logger.error("抽出された項目がありません")
        return

    logger.info(f"抽出された項目数: {len(price_refs)}")

    # Show extracted items by discipline
    discipline_counts = {}
    for ref in price_refs:
        disc = ref.discipline.value if hasattr(ref.discipline, 'value') else str(ref.discipline)
        discipline_counts[disc] = discipline_counts.get(disc, 0) + 1

    logger.info("工事区分別項目数:")
    for disc, count in discipline_counts.items():
        logger.info(f"  {disc}: {count}項目")

    # Merge with existing KB
    logger.info("既存KBとマージ中...")
    merged_refs = kb_builder.merge_with_existing_kb(
        price_refs,
        merge_strategy="keep_new"  # New data takes priority
    )

    # Save merged KB
    output_path = "kb/price_kb.json"
    kb_builder.save_kb_to_json(merged_refs, output_path)

    logger.info(f"KBを保存しました: {output_path}")
    logger.info(f"総項目数: {len(merged_refs)}")

    # Show sample items
    logger.info("\n抽出された項目サンプル:")
    for ref in price_refs[:10]:
        spec = ref.features.get("specification", "") if ref.features else ""
        logger.info(f"  - {ref.description} ({spec}) @{ref.unit_price:,.0f}円/{ref.unit}")


if __name__ == "__main__":
    main()
