#!/usr/bin/env python3
"""
KB品質改善スクリプト

問題点を修正:
1. 工事区分の誤分類を修正
2. 重複項目を統合
3. 汎用的すぎる項目名に仕様を追加
4. 仕様が空の項目を補完
5. 異常な高額項目にフラグを追加
"""

import json
import re
from pathlib import Path
from collections import defaultdict

def load_kb():
    with open('kb/price_kb.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def save_kb(kb_items):
    with open('kb/price_kb.json', 'w', encoding='utf-8') as f:
        json.dump(kb_items, f, ensure_ascii=False, indent=2)

def fix_discipline_classification(kb_items):
    """工事区分の誤分類を修正"""
    print("\n■ 工事区分の誤分類を修正中...")

    # キーワードベースの分類ルール
    classification_rules = {
        "衛生設備工事": [
            "給水", "排水", "給湯", "トイレ", "便器", "洗面", "受水槽",
            "水道用", "塩化ビニル管", "塩ビ管", "VP", "VU", "HIVP",
            "汚水", "雑排水", "衛生器具", "洗浄", "蛇口", "水栓"
        ],
        "空調設備工事": [
            "エアコン", "空調", "冷媒", "室外機", "室内機", "ヒートポンプ",
            "パッケージ", "ダクト", "換気", "ファン", "全熱交換", "送風"
        ],
        "ガス設備工事": [
            "ガス管", "ガス栓", "ガスコンセント", "PE管", "ガスメーター",
            "都市ガス", "LPガス", "ガス給湯", "ガス漏れ", "気密試験"
        ],
        "消防設備工事": [
            "スプリンクラー", "消火栓", "消火器", "感知器", "火災報知",
            "誘導灯", "非常放送", "避難", "防災"
        ],
    }

    fixed_count = 0
    for item in kb_items:
        desc = item.get('description', '')
        current_disc = item.get('discipline', '')

        # 再分類が必要かチェック
        for new_disc, keywords in classification_rules.items():
            if any(kw in desc for kw in keywords):
                if current_disc != new_disc:
                    # 電気設備に誤分類されているケースを修正
                    if current_disc == "電気設備工事" and new_disc in ["衛生設備工事", "空調設備工事"]:
                        item['discipline'] = new_disc
                        item['discipline_corrected'] = True
                        item['original_discipline'] = current_disc
                        fixed_count += 1
                break

    print(f"  修正: {fixed_count}件")
    return fixed_count

def merge_duplicate_items(kb_items):
    """重複項目を統合（同じ項目名+仕様の場合、価格を平均化）"""
    print("\n■ 重複項目を統合中...")

    # キー: (description, specification, unit, discipline)
    item_groups = defaultdict(list)

    for item in kb_items:
        desc = item.get('description', '')
        spec = item.get('features', {}).get('specification', '')
        unit = item.get('unit', '')
        disc = item.get('discipline', '')

        key = (desc, spec, unit, disc)
        item_groups[key].append(item)

    # 重複を統合
    merged_items = []
    merged_count = 0

    for key, items in item_groups.items():
        if len(items) == 1:
            merged_items.append(items[0])
        else:
            # 価格を平均化
            prices = [i.get('unit_price', 0) for i in items if i.get('unit_price')]
            if prices:
                avg_price = sum(prices) / len(prices)

                # 最初の項目をベースに、価格を更新
                base_item = items[0].copy()
                base_item['unit_price'] = round(avg_price, 0)
                base_item['merged_from'] = len(items)
                base_item['price_range'] = f"¥{min(prices):,.0f} - ¥{max(prices):,.0f}"

                merged_items.append(base_item)
                merged_count += len(items) - 1
            else:
                merged_items.append(items[0])

    print(f"  統合: {merged_count}件 ({len(kb_items)} → {len(merged_items)})")
    return merged_items

def enhance_generic_items(kb_items):
    """汎用的すぎる項目名を改善"""
    print("\n■ 汎用的な項目名を改善中...")

    # 項目名+工事区分から推測できる仕様を追加
    enhancements = {
        ("接地工事", "電気設備工事"): {"default_spec": "A種・B種・C種・D種"},
        ("諸経費", ""): {"default_spec": "現場管理費・一般管理費"},
        ("解体費", ""): {"default_spec": "既設撤去"},
        ("貫通工事", ""): {"default_spec": "壁・床貫通"},
        ("保温工事", ""): {"default_spec": "配管保温"},
    }

    enhanced_count = 0
    for item in kb_items:
        desc = item.get('description', '')
        disc = item.get('discipline', '')
        spec = item.get('features', {}).get('specification', '')

        # 仕様が空の場合のみ補完
        if not spec:
            for (key_desc, key_disc), enhancement in enhancements.items():
                if desc == key_desc and (not key_disc or disc == key_disc):
                    if 'features' not in item:
                        item['features'] = {}
                    item['features']['specification'] = enhancement['default_spec']
                    item['spec_enhanced'] = True
                    enhanced_count += 1
                    break

    print(f"  改善: {enhanced_count}件")
    return enhanced_count

def flag_problematic_items(kb_items):
    """問題のある項目にフラグを追加"""
    print("\n■ 問題項目にフラグを追加中...")

    flagged_count = 0
    for item in kb_items:
        desc = item.get('description', '')
        price = item.get('unit_price', 0) or 0
        unit = item.get('unit', '')
        spec = item.get('features', {}).get('specification', '')

        issues = []

        # 短すぎる項目名
        if len(desc) <= 2:
            issues.append("short_name")

        # 仕様なしの高額項目
        if not spec and price > 50000:
            issues.append("missing_spec_high_value")

        # 500万円超の一式
        if price > 5000000 and unit == '式':
            issues.append("extremely_high_lump_sum")
            item['requires_exact_match'] = True

        # 一時的なID
        if item.get('item_id', '').startswith('tmp'):
            issues.append("temporary_id")

        if issues:
            item['quality_issues'] = issues
            flagged_count += 1

    print(f"  フラグ追加: {flagged_count}件")
    return flagged_count

def clean_temporary_ids(kb_items):
    """一時的なIDを正式なIDに変更"""
    print("\n■ 一時的なIDを修正中...")

    # 工事区分別のカウンター
    counters = defaultdict(int)
    prefix_map = {
        "電気設備工事": "ELEC",
        "機械設備工事": "MECH",
        "衛生設備工事": "PLMB",
        "空調設備工事": "HVAC",
        "ガス設備工事": "GAS",
        "消防設備工事": "FIRE",
    }

    # 既存のIDからカウンターを初期化
    for item in kb_items:
        item_id = item.get('item_id', '')
        for disc, prefix in prefix_map.items():
            if item_id.startswith(prefix):
                try:
                    num = int(item_id.split('_')[-1])
                    counters[disc] = max(counters[disc], num)
                except:
                    pass

    # 一時的なIDを更新
    fixed_count = 0
    for item in kb_items:
        item_id = item.get('item_id', '')
        if item_id.startswith('tmp') or not item_id:
            disc = item.get('discipline', '不明')
            prefix = prefix_map.get(disc, 'UNK')
            counters[disc] += 1
            item['item_id'] = f"{prefix}_{counters[disc]:04d}"
            item['id_cleaned'] = True
            fixed_count += 1

    print(f"  修正: {fixed_count}件")
    return fixed_count

def generate_quality_report(kb_items):
    """品質レポートを生成"""
    print("\n" + "=" * 60)
    print("【KB品質レポート】")
    print("=" * 60)

    total = len(kb_items)

    # 工事区分別
    by_discipline = defaultdict(int)
    for item in kb_items:
        by_discipline[item.get('discipline', '不明')] += 1

    print(f"\n■ 総項目数: {total}")
    print("\n■ 工事区分別:")
    for disc, count in sorted(by_discipline.items(), key=lambda x: -x[1]):
        print(f"    {disc}: {count}件 ({count/total*100:.1f}%)")

    # 品質問題のある項目
    with_issues = [i for i in kb_items if i.get('quality_issues')]
    print(f"\n■ 品質問題のある項目: {len(with_issues)}件")

    issue_counts = defaultdict(int)
    for item in with_issues:
        for issue in item.get('quality_issues', []):
            issue_counts[issue] += 1

    for issue, count in sorted(issue_counts.items(), key=lambda x: -x[1]):
        print(f"    {issue}: {count}件")

    # 修正された項目
    corrected = len([i for i in kb_items if i.get('discipline_corrected')])
    merged = len([i for i in kb_items if i.get('merged_from')])
    enhanced = len([i for i in kb_items if i.get('spec_enhanced')])
    id_cleaned = len([i for i in kb_items if i.get('id_cleaned')])

    print(f"\n■ 今回の修正:")
    print(f"    工事区分修正: {corrected}件")
    print(f"    重複統合: {merged}件")
    print(f"    仕様補完: {enhanced}件")
    print(f"    ID修正: {id_cleaned}件")

def main():
    print("=" * 60)
    print("KB品質改善スクリプト")
    print("=" * 60)

    # KBを読み込み
    kb_items = load_kb()
    print(f"\n元のKB項目数: {len(kb_items)}")

    # バックアップ
    backup_path = Path('kb/price_kb_backup_quality.json')
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(kb_items, f, ensure_ascii=False, indent=2)
    print(f"バックアップ保存: {backup_path}")

    # 改善処理
    fix_discipline_classification(kb_items)
    kb_items = merge_duplicate_items(kb_items)
    enhance_generic_items(kb_items)
    flag_problematic_items(kb_items)
    clean_temporary_ids(kb_items)

    # 保存
    save_kb(kb_items)
    print(f"\n改善後のKB保存: kb/price_kb.json")

    # レポート生成
    generate_quality_report(kb_items)

    print("\n" + "=" * 60)
    print("KB品質改善完了")
    print("=" * 60)

if __name__ == "__main__":
    main()
