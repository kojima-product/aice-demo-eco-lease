# 積算AI精度向上 改善計画

## 現状の問題点サマリー

| 問題 | 影響度 | 改善難易度 |
|------|--------|------------|
| KB項目の表記ゆれ（半角/全角、略称） | ★★★ 高 | ★ 低 |
| 「同上」「〃」項目が354件 (18%) | ★★★ 高 | ★★ 中 |
| ガス設備KBが51件のみ | ★★★ 高 | ★★ 中 |
| AI生成項目名とKB項目名のギャップ | ★★★ 高 | ★★ 中 |
| 高額一式項目の誤マッチ | ★★ 中 | ★ 低 |

---

## Phase 1: 即効性のある改善（1-2日）

### 1.1 KB項目名の正規化

```python
# 実装すべき正規化ルール
def normalize_kb_item(description):
    # 半角カタカナ→全角
    text = text.replace('ｶﾞ', 'ガ').replace('ｽ', 'ス')
    text = text.replace('ﾈｼﾞ', 'ネジ').replace('ﾎﾟﾘ', 'ポリ')
    text = text.replace('ｹｰﾌﾞﾙ', 'ケーブル').replace('ｴﾁﾚﾝ', 'エチレン')

    # 「〃」項目に親項目名を継承
    # → 別途処理が必要

    return text
```

### 1.2 AI生成プロンプトにKB語彙を含める

現在のプロンプト:
```
"白ガス管の配管延長 = ガス栓数 × 15〜25m"
```

改善後のプロンプト:
```
【重要】以下のKB登録項目名を使用してください：
- 白ガス管（ネジ接合）
- 照明器具(LED)
- 架橋ポリエチレンケーブル（CVT）
- キュービクル
...
```

### 1.3 高額一式項目の除外

```python
# 500万円超の「式」単価を除外
if kb_price > 5000000 and kb_unit == '式':
    return False  # マッチング対象外
```

---

## Phase 2: KBデータの拡充（1週間）

### 2.1 ガス設備KBの拡充

現状: 51件 → 目標: 200件以上

必要な追加項目:
```
■ 配管材料
  - 白ガス管 15A, 20A, 25A, 32A, 40A, 50A, 65A, 80A （各サイズ）
  - カラー鋼管 各サイズ
  - PE管 各サイズ
  - フレキ管 各サイズ

■ 継手・バルブ
  - エルボ、チーズ、レデューサー
  - ボールバルブ、ゲートバルブ
  - フランジ、ユニオン

■ 工事費
  - 配管撤去（既存）
  - 穴あけ、穴補修
  - コア抜き
  - 防火区画貫通処理
```

### 2.2 「同上」「〃」項目の親項目継承

```python
# 354件の「同上」項目を親項目名で補完
for i, item in enumerate(kb_items):
    if item['description'] in ['〃', '同上', '']:
        # 直前の有効な項目名を継承
        for j in range(i-1, -1, -1):
            if kb_items[j]['description'] not in ['〃', '同上', '']:
                item['description'] = kb_items[j]['description']
                break
```

---

## Phase 3: マッチングロジックの改善（2週間）

### 3.1 類義語辞書の拡充

現在: 約100語 → 目標: 500語以上

追加すべき類義語:
```python
SYNONYM_ADDITIONS = {
    "LED照明": ["照明器具(LED)", "LED器具", "LED灯", "LEDベースライト"],
    "高圧ケーブル": ["架橋ポリエチレン", "CV", "CVT", "CVQ"],
    "配管撤去": ["既設配管撤去", "撤去工事", "解体撤去"],
    "穴補修": ["穴あけ補修", "貫通補修", "スリーブ処理"],
}
```

### 3.2 ベクトル検索の改善

現在: `intfloat/multilingual-e5-small`

改善案:
1. 日本語専用モデル `cl-tohoku/bert-base-japanese` への変更
2. Fine-tuningによる見積専用モデルの構築

---

## Phase 4: 検証・モニタリング（継続）

### 4.1 マッチング率の自動計測

```python
# 毎回の見積生成でマッチング率を記録
metrics = {
    'total_items': len(items),
    'matched_items': len([i for i in items if i.unit_price]),
    'match_rate': matched / total,
    'total_amount': sum(i.amount or 0 for i in items),
    'timestamp': datetime.now()
}
save_metrics(metrics)
```

### 4.2 エラーパターンの収集

```python
# マッチング失敗した項目を自動収集
if not matched:
    log_unmatched_item({
        'name': item.name,
        'specification': item.specification,
        'discipline': item.discipline,
        'timestamp': datetime.now()
    })
```

---

## 期待される効果

| Phase | 期間 | マッチング率向上 |
|-------|------|------------------|
| Phase 1 | 1-2日 | 現状 → +15〜20% |
| Phase 2 | 1週間 | +20〜30% |
| Phase 3 | 2週間 | +10〜15% |
| **合計** | 3週間 | **50% → 85%以上** |

---

## 実装優先度

1. **最優先**: AI生成プロンプトにKB語彙を含める（即効性高）
2. **優先度高**: 「同上」「〃」項目の親項目継承
3. **優先度高**: ガス設備KBの拡充
4. **優先度中**: 類義語辞書の拡充
5. **優先度中**: ベクトル検索モデルの変更
