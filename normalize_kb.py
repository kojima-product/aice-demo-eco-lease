#!/usr/bin/env python3
"""
KBデータ正規化スクリプト

以下の処理を実行:
1. 半角カタカナ→全角カタカナ変換
2. 括弧の統一（全角→半角）
3. 「同上」「〃」項目に親項目名を継承
4. 空descriptionの処理
5. 高額一式項目にフラグを追加
"""

import json
import unicodedata
import re
from pathlib import Path
from copy import deepcopy


def normalize_text(text: str) -> str:
    """テキストを正規化"""
    if not text:
        return ""

    # NFKC正規化（半角カタカナ→全角カタカナ、全角英数→半角英数）
    text = unicodedata.normalize('NFKC', text)

    # 全角括弧→半角
    text = text.replace('（', '(').replace('）', ')')

    # 全角スペース→半角
    text = text.replace('　', ' ')

    # 複数スペースを1つに
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


def resolve_ditto_items(kb_items: list) -> tuple[list, int]:
    """「同上」「〃」項目に親項目名を継承"""
    ditto_patterns = ['〃', '同上', '上記と同じ', '上記同', '″']
    resolved_count = 0
    last_valid_description = ""
    last_valid_discipline = ""

    for item in kb_items:
        desc = item.get('description', '')

        # descriptionが空か、dittoパターンに該当するか
        is_ditto = not desc or any(
            desc.strip() == pattern or desc.strip().startswith(pattern)
            for pattern in ditto_patterns
        )

        if is_ditto and last_valid_description:
            # 「同上施工費」のように接尾語がある場合は保持
            suffix = ""
            for pattern in ditto_patterns:
                if desc.strip().startswith(pattern):
                    suffix = desc.strip()[len(pattern):]
                    break

            item['description'] = last_valid_description + suffix if suffix else last_valid_description
            item['inherited_from'] = True
            item['original_description'] = desc

            # disciplineも継承
            if not item.get('discipline') and last_valid_discipline:
                item['discipline'] = last_valid_discipline

            resolved_count += 1
        else:
            # 有効な項目名を記録
            if desc and len(desc) > 1:
                last_valid_description = desc
                last_valid_discipline = item.get('discipline', '')

    return kb_items, resolved_count


def normalize_units(kb_items: list) -> list:
    """単位を正規化"""
    unit_mappings = {
        'ヶ所': '箇所',
        'ケ所': '箇所',
        'ｹ所': '箇所',
        'カ所': '箇所',
        '個所': '箇所',
        'ヵ所': '箇所',
        '〃': None,  # 前の単位を継承
    }

    last_valid_unit = ""
    for item in kb_items:
        unit = item.get('unit', '')

        if unit == '〃' and last_valid_unit:
            item['unit'] = last_valid_unit
            item['unit_inherited'] = True
        elif unit in unit_mappings:
            if unit_mappings[unit] is None:
                if last_valid_unit:
                    item['unit'] = last_valid_unit
                    item['unit_inherited'] = True
            else:
                item['unit'] = unit_mappings[unit]
        else:
            if unit:
                last_valid_unit = unit

    return kb_items


def flag_high_value_items(kb_items: list) -> tuple[list, int]:
    """500万円超の一式項目にフラグを追加"""
    flagged_count = 0
    for item in kb_items:
        price = item.get('unit_price', 0) or 0
        unit = item.get('unit', '')

        if price > 5_000_000 and unit == '式':
            item['high_value_lump_sum'] = True
            item['requires_exact_match'] = True
            flagged_count += 1

    return kb_items, flagged_count


def analyze_kb(kb_items: list) -> dict:
    """KB分析レポートを生成"""
    stats = {
        'total_items': len(kb_items),
        'by_discipline': {},
        'empty_descriptions': 0,
        'ditto_items': 0,
        'half_width_katakana': 0,
        'high_value_lump_sum': 0,
        'unit_types': {},
    }

    ditto_patterns = ['〃', '同上', '上記と同じ', '上記同', '″']
    half_width_pattern = re.compile(r'[\uff61-\uff9f]')

    for item in kb_items:
        # 工事区分別
        disc = item.get('discipline', '不明')
        stats['by_discipline'][disc] = stats['by_discipline'].get(disc, 0) + 1

        # 空description
        desc = item.get('description', '')
        if not desc or len(desc) <= 1:
            stats['empty_descriptions'] += 1

        # 同上項目
        if any(desc.strip() == p or desc.strip().startswith(p) for p in ditto_patterns):
            stats['ditto_items'] += 1

        # 半角カタカナ
        if half_width_pattern.search(desc):
            stats['half_width_katakana'] += 1

        # 高額一式
        price = item.get('unit_price', 0) or 0
        unit = item.get('unit', '')
        if price > 5_000_000 and unit == '式':
            stats['high_value_lump_sum'] += 1

        # 単位種類
        if unit:
            stats['unit_types'][unit] = stats['unit_types'].get(unit, 0) + 1

    return stats


def main():
    kb_path = Path('kb/price_kb.json')
    backup_path = Path('kb/price_kb_backup_normalized.json')

    print("=" * 60)
    print("KBデータ正規化スクリプト")
    print("=" * 60)

    # KBを読み込み
    with open(kb_path, 'r', encoding='utf-8') as f:
        kb_items = json.load(f)

    # 正規化前の分析
    print("\n【正規化前の状態】")
    before_stats = analyze_kb(kb_items)
    print(f"  総項目数: {before_stats['total_items']}")
    print(f"  空description: {before_stats['empty_descriptions']}")
    print(f"  「同上」「〃」項目: {before_stats['ditto_items']}")
    print(f"  半角カタカナ含む: {before_stats['half_width_katakana']}")
    print(f"  高額一式(500万超): {before_stats['high_value_lump_sum']}")
    print("\n  工事区分別:")
    for disc, count in sorted(before_stats['by_discipline'].items(), key=lambda x: -x[1]):
        print(f"    {disc}: {count}件")

    # バックアップ作成
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(kb_items, f, ensure_ascii=False, indent=2)
    print(f"\n  バックアップ保存: {backup_path}")

    # 正規化処理
    print("\n【正規化処理】")

    # 1. テキスト正規化（半角カタカナ→全角等）
    for item in kb_items:
        item['description'] = normalize_text(item.get('description', ''))
        if 'features' in item and 'specification' in item['features']:
            item['features']['specification'] = normalize_text(item['features'].get('specification', ''))
    print("  ✓ テキスト正規化完了（半角カタカナ→全角）")

    # 2. 単位正規化
    kb_items = normalize_units(kb_items)
    print("  ✓ 単位正規化完了")

    # 3. 「同上」項目の解決
    kb_items, ditto_resolved = resolve_ditto_items(kb_items)
    print(f"  ✓ 「同上」項目解決: {ditto_resolved}件")

    # 4. 高額一式項目のフラグ付け
    kb_items, flagged = flag_high_value_items(kb_items)
    print(f"  ✓ 高額一式項目フラグ: {flagged}件")

    # 正規化後の分析
    print("\n【正規化後の状態】")
    after_stats = analyze_kb(kb_items)
    print(f"  総項目数: {after_stats['total_items']}")
    print(f"  空description: {after_stats['empty_descriptions']}")
    print(f"  「同上」「〃」項目: {after_stats['ditto_items']}")
    print(f"  半角カタカナ含む: {after_stats['half_width_katakana']}")

    # 保存
    with open(kb_path, 'w', encoding='utf-8') as f:
        json.dump(kb_items, f, ensure_ascii=False, indent=2)
    print(f"\n  正規化後のKB保存: {kb_path}")

    # 改善サマリー
    print("\n【改善サマリー】")
    print(f"  半角カタカナ: {before_stats['half_width_katakana']} → {after_stats['half_width_katakana']} ({before_stats['half_width_katakana'] - after_stats['half_width_katakana']}件解消)")
    print(f"  「同上」項目: {before_stats['ditto_items']} → {after_stats['ditto_items']} ({ditto_resolved}件継承)")
    print(f"  高額一式フラグ: {flagged}件にフラグ追加")

    print("\n" + "=" * 60)
    print("正規化完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
