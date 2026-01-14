#!/usr/bin/env python3
"""
マッチング状況のデバッグ・可視化スクリプト

現在のKBとAI生成項目のマッチング状況を分析し、
精度向上のための改善点を特定します。
"""

import json
import re
from pathlib import Path
from collections import defaultdict

def load_kb():
    """KBを読み込み"""
    with open('kb/price_kb.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def normalize_text(text):
    """テキストを正規化"""
    if not text:
        return ""
    # 全角→半角
    text = text.replace('（', '(').replace('）', ')').replace('　', ' ')
    text = text.replace('ｶﾞ', 'ガ').replace('ｽ', 'ス').replace('ﾈｼﾞ', 'ネジ')
    text = text.replace('ﾎﾟﾘ', 'ポリ').replace('ｹｰﾌﾞﾙ', 'ケーブル')
    text = text.replace('ｴﾁﾚﾝ', 'エチレン')
    # 記号の統一
    text = text.replace('・', '').replace('/', '').replace('-', '')
    # 複数空白を1つに
    text = re.sub(r'\s+', ' ', text)
    return text.strip().lower()

def analyze_kb_structure(kb):
    """KB構造の分析"""
    print("=" * 80)
    print("【KB構造分析】")
    print("=" * 80)

    # 工事区分別
    disciplines = defaultdict(list)
    units = defaultdict(int)
    price_ranges = {}

    for item in kb:
        disc = item.get('discipline', '不明')
        disciplines[disc].append(item)

        unit = item.get('unit', '不明')
        units[unit] += 1

        price = item.get('unit_price', 0)
        if price:
            if disc not in price_ranges:
                price_ranges[disc] = {'min': price, 'max': price, 'prices': []}
            price_ranges[disc]['min'] = min(price_ranges[disc]['min'], price)
            price_ranges[disc]['max'] = max(price_ranges[disc]['max'], price)
            price_ranges[disc]['prices'].append(price)

    print(f"\n■ 総項目数: {len(kb)}\n")

    print("■ 工事区分別項目数:")
    for disc, items in sorted(disciplines.items(), key=lambda x: -len(x[1])):
        pct = len(items) / len(kb) * 100
        print(f"  {disc}: {len(items)}件 ({pct:.1f}%)")

    print("\n■ 単位別項目数 (上位10):")
    for unit, count in sorted(units.items(), key=lambda x: -x[1])[:10]:
        print(f"  '{unit}': {count}件")

    print("\n■ 工事区分別の単価範囲:")
    for disc, pr in price_ranges.items():
        if pr['prices']:
            avg = sum(pr['prices']) / len(pr['prices'])
            print(f"  {disc}: ¥{pr['min']:,.0f} - ¥{pr['max']:,.0f} (平均: ¥{avg:,.0f})")

    return disciplines, units

def find_similar_items(kb, search_term, discipline=None, limit=10):
    """類似項目を検索"""
    search_norm = normalize_text(search_term)
    results = []

    for item in kb:
        if discipline and item.get('discipline') != discipline:
            continue

        desc = item.get('description', '')
        desc_norm = normalize_text(desc)
        spec = item.get('features', {}).get('specification', '')
        full_text = f"{desc} {spec}"
        full_norm = normalize_text(full_text)

        score = 0

        # 完全一致
        if search_norm == desc_norm:
            score = 100
        # 部分一致
        elif search_norm in desc_norm:
            score = 80
        elif desc_norm in search_norm:
            score = 70
        # 単語一致
        else:
            search_words = set(search_norm.split())
            desc_words = set(desc_norm.split())
            common = search_words & desc_words
            if common:
                score = len(common) / max(len(search_words), len(desc_words)) * 60

        if score > 0:
            results.append({
                'description': desc,
                'specification': spec,
                'unit': item.get('unit'),
                'unit_price': item.get('unit_price'),
                'discipline': item.get('discipline'),
                'score': score
            })

    results.sort(key=lambda x: -x['score'])
    return results[:limit]

def test_typical_items():
    """よくある項目でマッチングをテスト"""
    print("\n" + "=" * 80)
    print("【マッチングテスト】")
    print("=" * 80)

    kb = load_kb()

    # AIが生成しそうな項目名のリスト
    test_items = [
        # ガス設備
        ("白ガス管", "ガス設備工事", "15A"),
        ("白ガス管（ネジ接合）", "ガス設備工事", "20A"),
        ("PE管", "ガス設備工事", "50A"),
        ("ガス栓", "ガス設備工事", ""),
        ("ガスコンセント", "ガス設備工事", ""),
        ("配管撤去", "ガス設備工事", ""),
        ("気密試験", "ガス設備工事", ""),
        ("諸経費", "ガス設備工事", ""),

        # 電気設備
        ("キュービクル", "電気設備工事", "300kVA"),
        ("高圧ケーブル", "電気設備工事", "CV 38sq"),
        ("分電盤", "電気設備工事", "主幹100A"),
        ("LED照明器具", "電気設備工事", "40W"),
        ("コンセント", "電気設備工事", "2P15A"),
        ("誘導灯", "電気設備工事", "B級"),
        ("感知器", "電気設備工事", "煙感知器"),

        # 機械設備
        ("エアコン", "機械設備工事", "4.0kW"),
        ("パッケージエアコン", "機械設備工事", "5.6kW"),
        ("換気扇", "機械設備工事", ""),
        ("給水ポンプ", "機械設備工事", ""),
        ("排水管", "機械設備工事", "VU 100A"),
    ]

    results_summary = {
        'success': 0,
        'partial': 0,
        'fail': 0
    }

    for item_name, discipline, spec in test_items:
        search_term = f"{item_name} {spec}".strip()
        matches = find_similar_items(kb, search_term, discipline, limit=3)

        print(f"\n検索: 「{item_name}」 {spec} [{discipline}]")

        if matches:
            best = matches[0]
            if best['score'] >= 70:
                status = "✓ マッチ"
                results_summary['success'] += 1
            else:
                status = "△ 部分マッチ"
                results_summary['partial'] += 1

            print(f"  {status}: {best['description']} ({best['specification']}) "
                  f"@¥{best['unit_price']:,.0f}/{best['unit']} [score={best['score']:.0f}]")

            if len(matches) > 1:
                print(f"  候補2: {matches[1]['description']} [score={matches[1]['score']:.0f}]")
        else:
            print(f"  ✗ マッチなし")
            results_summary['fail'] += 1

    print("\n" + "-" * 40)
    total = len(test_items)
    print(f"【マッチング結果サマリー】")
    print(f"  成功: {results_summary['success']}/{total} ({results_summary['success']/total*100:.0f}%)")
    print(f"  部分: {results_summary['partial']}/{total} ({results_summary['partial']/total*100:.0f}%)")
    print(f"  失敗: {results_summary['fail']}/{total} ({results_summary['fail']/total*100:.0f}%)")

def find_kb_gaps():
    """KBに不足している項目を特定"""
    print("\n" + "=" * 80)
    print("【KB不足項目の特定】")
    print("=" * 80)

    kb = load_kb()

    # 必要な項目カテゴリ
    required_categories = {
        'ガス設備工事': [
            '白ガス管', 'カラー鋼管', 'PE管', 'ガス栓', 'ガスコンセント',
            'ネジコック', '分岐コック', 'ガスメーター', '気密試験',
            '配管撤去', '配管支持金物', '穴補修', '諸経費'
        ],
        '電気設備工事': [
            'キュービクル', '変圧器', '高圧ケーブル', '分電盤', '動力盤',
            'LED照明', '誘導灯', '非常照明', 'コンセント', 'LAN配線',
            '感知器', '避雷針', '接地工事', '電気試験'
        ],
        '機械設備工事': [
            'エアコン', 'パッケージエアコン', '室外機', '冷媒配管',
            '換気扇', '全熱交換器', 'ダクト', '給水管', '排水管',
            '給水ポンプ', '衛生器具', '給湯器', '保温工事'
        ]
    }

    for discipline, categories in required_categories.items():
        print(f"\n■ {discipline}")
        discipline_items = [item for item in kb if item.get('discipline') == discipline]

        for category in categories:
            matches = [item for item in discipline_items
                      if category.lower() in normalize_text(item.get('description', ''))]

            if len(matches) >= 3:
                status = "✓"
            elif len(matches) >= 1:
                status = "△"
            else:
                status = "✗"

            print(f"  {status} {category}: {len(matches)}件")

def main():
    print("積算AI マッチング状況分析ツール")
    print("=" * 80)

    kb = load_kb()

    # 1. KB構造分析
    analyze_kb_structure(kb)

    # 2. マッチングテスト
    test_typical_items()

    # 3. 不足項目特定
    find_kb_gaps()

    print("\n" + "=" * 80)
    print("【改善提案】")
    print("=" * 80)
    print("""
1. 即効性のある改善:
   - AI生成プロンプトにKBの項目名リストを含める
   - 類義語辞書を拡充する（特に略称・正式名称の対応）

2. KBの拡充:
   - ガス設備: 51件 → 200件以上
   - 仮設・試験・諸経費カテゴリの追加

3. 正規化の強化:
   - 半角カタカナ→全角カタカナ
   - 単位の統一（ケ所/ヶ所→箇所）
   - 仕様フォーマットの統一（15A/15Φ/φ15→15A）
""")

if __name__ == "__main__":
    main()
