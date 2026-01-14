#!/usr/bin/env python3
"""
見積算出ロジック検証・整合性チェッカー

仕様書と見積書の間の算出ロジックを解明し、
AI生成見積との整合性をチェックする機能を提供します。
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json


class CalculationBasis(str, Enum):
    """算出根拠タイプ"""
    MATERIAL_QTY = "材料費×数量"          # 材料単価 × 数量
    LABOR_DAYS = "労務費×日数"            # 作業員単価 × 人数 × 日数
    METER_LENGTH = "m単価×延長"           # m単価 × 延長m
    AREA_RATE = "㎡単価×面積"             # ㎡単価 × 面積
    LUMP_SUM = "一式計上"                 # 工種単位で一式
    PERCENTAGE = "率計算"                 # 基礎額 × 率（諸経費等）
    SPEC_EXPLICIT = "仕様書明記"          # 仕様書に数量が明記
    AREA_ESTIMATE = "面積から推定"         # 床面積から推定
    ROOM_ESTIMATE = "部屋数から推定"       # 部屋数から推定
    KB_REFERENCE = "過去実績参照"          # KBから単価を参照
    SUBTOTAL = "子項目の合計"              # 親項目：子項目の合計
    UNKNOWN = "根拠不明"                   # 根拠が不明


@dataclass
class CalculationTrace:
    """算出トレース（1項目の算出経緯）"""
    item_name: str                          # 項目名
    spec_text: Optional[str] = None         # 仕様書該当箇所
    quantity: Optional[float] = None        # 数量
    unit: Optional[str] = None              # 単位
    unit_price: Optional[float] = None      # 単価
    amount: Optional[float] = None          # 金額
    calculation_basis: CalculationBasis = CalculationBasis.UNKNOWN
    calculation_formula: Optional[str] = None  # 計算式（例: "8,990円/m × 93m"）
    kb_reference: Optional[str] = None      # KB参照ID
    confidence: float = 0.0                 # 算出信頼度
    notes: Optional[str] = None             # 備考


@dataclass
class SpecExtraction:
    """仕様書から抽出した情報"""
    project_name: str = ""
    building_area_m2: Optional[float] = None     # 延床面積
    building_floors: Optional[int] = None        # 階数
    room_count: Optional[int] = None             # 部屋数
    gas_connection_points: Optional[int] = None  # ガス接続箇所
    electrical_capacity_kva: Optional[float] = None  # 電気容量
    lighting_count: Optional[int] = None         # 照明数
    outlet_count: Optional[int] = None           # コンセント数
    required_equipment: List[str] = field(default_factory=list)  # 必要設備リスト
    raw_text: str = ""                           # 抽出テキスト


@dataclass
class VerificationResult:
    """検証結果"""
    item_name: str
    ai_amount: float                  # AI生成金額
    human_amount: Optional[float]     # 人間作成金額（参照見積がある場合）
    difference: Optional[float]       # 差額
    difference_ratio: Optional[float] # 差異率
    calculation_trace: CalculationTrace  # 算出トレース
    match_status: str                 # マッチ状態: "一致", "許容範囲", "要確認", "未マッチ"
    issues: List[str] = field(default_factory=list)  # 検出された問題


class EstimateVerifier:
    """見積検証器"""

    # 推定ルール: 項目名パターン → (推定方法, 係数)
    ESTIMATION_RULES = {
        # 電気設備
        "照明": ("面積", 0.08),      # 8台/100㎡
        "コンセント": ("面積", 0.15), # 15個/100㎡
        "スイッチ": ("部屋数", 2.0),  # 2個/部屋
        "分電盤": ("階数", 2.0),      # 2面/階
        "ケーブル": ("面積", 0.5),    # 50m/100㎡

        # ガス設備
        "ガス管": ("面積", 0.3),      # 30m/100㎡
        "ガス栓": ("部屋数", 0.5),    # 0.5個/部屋

        # 共通
        "配管": ("面積", 0.4),        # 40m/100㎡
    }

    # 法定福利費率
    STATUTORY_WELFARE_RATE = 0.1607  # 16.07%

    def __init__(self, kb_path: str = "kb/price_kb.json"):
        """初期化"""
        self.kb_data = self._load_kb(kb_path)

    def _load_kb(self, kb_path: str) -> List[Dict]:
        """KBを読み込み"""
        path = Path(kb_path)
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

    def extract_spec_info(self, spec_text: str) -> SpecExtraction:
        """仕様書から情報を抽出"""
        import re

        extraction = SpecExtraction()
        extraction.raw_text = spec_text[:2000]  # 最初の2000文字を保持

        # 延床面積を抽出（パターン拡張）
        area_patterns = [
            r'延床面積[：:\s]*([0-9,]+(?:\.[0-9]+)?)\s*[㎡m²]',
            r'床面積[：:\s]*([0-9,]+(?:\.[0-9]+)?)\s*[㎡m²]',
            r'([0-9,]+(?:\.[0-9]+)?)\s*[㎡m²].*延床',
            r'延べ面積[：:\s]*([0-9,]+(?:\.[0-9]+)?)\s*[㎡m²]',
            r'建築面積[：:\s]*([0-9,]+(?:\.[0-9]+)?)\s*[㎡m²]',
            # 表形式対応: "延床面積" と "2,145㎡" が別行の場合
            r'延床面積.*?([0-9,]+(?:\.[0-9]+)?)\s*[㎡m²]',
            r'合計.*?([0-9,]+(?:\.[0-9]+)?)\s*[㎡m²]',
            # 数字+㎡ の形式（最後の手段）
            r'([1-9][0-9]{2,4}(?:\.[0-9]+)?)\s*[㎡m²]',
        ]
        for pattern in area_patterns:
            match = re.search(pattern, spec_text, re.DOTALL)
            if match:
                area = float(match.group(1).replace(',', ''))
                # 妥当な面積範囲（100㎡〜100,000㎡）のみ採用
                if 100 <= area <= 100000:
                    extraction.building_area_m2 = area
                    break

        # 階数を抽出（パターン拡張）
        floor_patterns = [
            r'([0-9]+)\s*階建',
            r'地上\s*([0-9]+)\s*階',
            r'([0-9]+)F',
            r'([0-9]+)\s*階\s*構造',
            r'構造.*?([0-9]+)\s*階',
        ]
        for pattern in floor_patterns:
            match = re.search(pattern, spec_text)
            if match:
                floors = int(match.group(1))
                # 妥当な階数範囲（1〜50階）のみ採用
                if 1 <= floors <= 50:
                    extraction.building_floors = floors
                    break

        # 部屋数を推定（面積から）
        if extraction.building_area_m2:
            # 学校の場合: 約50㎡/室として推定
            extraction.room_count = int(extraction.building_area_m2 / 50)

        # 必要設備をキーワードから抽出
        equipment_keywords = [
            "キュービクル", "受変電設備", "分電盤", "照明", "コンセント",
            "LAN", "放送設備", "火災報知", "電話", "インターホン",
            "ガス配管", "ガス栓", "換気扇", "エアコン", "空調"
        ]
        for kw in equipment_keywords:
            if kw in spec_text:
                extraction.required_equipment.append(kw)

        return extraction

    def trace_calculation(
        self,
        item_name: str,
        quantity: Optional[float],
        unit: Optional[str],
        unit_price: Optional[float],
        amount: Optional[float],
        spec_extraction: Optional[SpecExtraction] = None,
        kb_match: Optional[Dict] = None
    ) -> CalculationTrace:
        """項目の算出経緯をトレース"""
        trace = CalculationTrace(
            item_name=item_name,
            quantity=quantity,
            unit=unit,
            unit_price=unit_price,
            amount=amount
        )

        # 親項目（小計）の検出: 単価がなく、金額がある場合は子項目の合計
        if (not unit_price or unit_price == 0) and amount and amount > 0:
            # 親項目と判断
            trace.calculation_basis = CalculationBasis.SUBTOTAL
            trace.calculation_formula = "子項目の合計"
            trace.confidence = 0.9
            trace.notes = "親項目（子項目の金額を集計）"
            return trace  # 親項目はここで処理完了

        # 単価の出所を特定
        if kb_match:
            trace.kb_reference = kb_match.get("item_id", "")
            trace.calculation_basis = CalculationBasis.KB_REFERENCE
            trace.notes = f"KB: {kb_match.get('description', '')}"

        # 数量の算出根拠を特定
        if quantity and spec_extraction:
            for keyword, (method, factor) in self.ESTIMATION_RULES.items():
                if keyword in item_name:
                    if method == "面積" and spec_extraction.building_area_m2:
                        estimated = spec_extraction.building_area_m2 * factor / 100
                        trace.calculation_basis = CalculationBasis.AREA_ESTIMATE
                        trace.notes = f"床面積{spec_extraction.building_area_m2}㎡ × {factor}/100㎡ = {estimated:.0f}"
                    elif method == "部屋数" and spec_extraction.room_count:
                        estimated = spec_extraction.room_count * factor
                        trace.calculation_basis = CalculationBasis.ROOM_ESTIMATE
                        trace.notes = f"部屋数{spec_extraction.room_count}室 × {factor}/室 = {estimated:.0f}"
                    elif method == "階数" and spec_extraction.building_floors:
                        estimated = spec_extraction.building_floors * factor
                        trace.calculation_basis = CalculationBasis.ROOM_ESTIMATE
                        trace.notes = f"階数{spec_extraction.building_floors}階 × {factor}/階 = {estimated:.0f}"
                    break

        # 計算式を生成
        if unit_price and quantity:
            if unit == "m":
                trace.calculation_formula = f"¥{unit_price:,.0f}/m × {quantity:.0f}m = ¥{quantity * unit_price:,.0f}"
                trace.calculation_basis = CalculationBasis.METER_LENGTH
            elif unit in ["個", "台", "灯", "箇所"]:
                trace.calculation_formula = f"¥{unit_price:,.0f}/{unit} × {quantity:.0f}{unit} = ¥{quantity * unit_price:,.0f}"
                trace.calculation_basis = CalculationBasis.MATERIAL_QTY
            elif unit == "式":
                trace.calculation_formula = f"¥{unit_price:,.0f}/式 × {quantity:.0f}式 = ¥{unit_price:,.0f}"
                trace.calculation_basis = CalculationBasis.LUMP_SUM

        # 信頼度を計算
        if trace.calculation_basis != CalculationBasis.UNKNOWN:
            trace.confidence = 0.8
        if trace.kb_reference:
            trace.confidence = min(trace.confidence + 0.1, 1.0)
        if trace.calculation_formula:
            trace.confidence = min(trace.confidence + 0.1, 1.0)

        return trace

    def verify_item(
        self,
        ai_item: Dict,
        human_item: Optional[Dict] = None,
        spec_extraction: Optional[SpecExtraction] = None
    ) -> VerificationResult:
        """項目を検証"""
        item_name = ai_item.get("name", "")
        ai_amount = ai_item.get("amount", 0) or 0

        # KB参照を探す
        kb_match = None
        for kb in self.kb_data:
            if kb.get("description", "") in item_name or item_name in kb.get("description", ""):
                kb_match = kb
                break

        # 算出トレースを生成
        trace = self.trace_calculation(
            item_name=item_name,
            quantity=ai_item.get("quantity"),
            unit=ai_item.get("unit"),
            unit_price=ai_item.get("unit_price"),
            amount=ai_amount,
            spec_extraction=spec_extraction,
            kb_match=kb_match
        )

        # 人間作成見積との比較
        human_amount = None
        difference = None
        difference_ratio = None
        match_status = "未マッチ"
        issues = []

        if human_item:
            human_amount = human_item.get("amount", 0) or 0
            if human_amount > 0:
                difference = ai_amount - human_amount
                difference_ratio = difference / human_amount if human_amount else 0

                if abs(difference_ratio) < 0.1:
                    match_status = "一致"
                elif abs(difference_ratio) < 0.3:
                    match_status = "許容範囲"
                else:
                    match_status = "要確認"
                    issues.append(f"金額差異が大きい: {difference_ratio:+.1%}")

        # 問題検出
        if ai_amount == 0:
            issues.append("金額が0円")
        if not ai_item.get("unit_price"):
            issues.append("単価未設定")
        if trace.calculation_basis == CalculationBasis.UNKNOWN:
            issues.append("算出根拠不明")

        return VerificationResult(
            item_name=item_name,
            ai_amount=ai_amount,
            human_amount=human_amount,
            difference=difference,
            difference_ratio=difference_ratio,
            calculation_trace=trace,
            match_status=match_status,
            issues=issues
        )

    def generate_verification_report(
        self,
        ai_items: List[Dict],
        human_items: Optional[List[Dict]] = None,
        spec_text: str = ""
    ) -> Dict:
        """検証レポートを生成"""
        # 仕様書から情報抽出
        spec_extraction = self.extract_spec_info(spec_text) if spec_text else None

        # 人間作成見積を項目名+仕様でインデックス化（より正確なマッチング）
        human_index = {}
        human_by_name_only = {}  # 名前のみのインデックス（フォールバック用）
        if human_items:
            for item in human_items:
                name = item.get("name", "")
                spec = item.get("specification", "") or ""
                if name:
                    # 名前+仕様でキー作成
                    key = f"{name}|{spec}".strip("|")
                    human_index[key] = item
                    # 名前のみも保存（複数ある場合は最後のもの）
                    if name not in human_by_name_only:
                        human_by_name_only[name] = []
                    human_by_name_only[name].append(item)

        # 各項目を検証
        results = []
        used_human_keys = set()  # 使用済みの参照項目を追跡

        for ai_item in ai_items:
            item_name = ai_item.get("name", "")
            ai_spec = ai_item.get("specification", "") or ""

            # 1. 名前+仕様で完全マッチを試行
            key = f"{item_name}|{ai_spec}".strip("|")
            human_item = human_index.get(key)

            # 2. 名前のみでマッチを試行（仕様が一致する未使用の項目を探す）
            if not human_item and item_name in human_by_name_only:
                for candidate in human_by_name_only[item_name]:
                    candidate_key = f"{candidate.get('name', '')}|{candidate.get('specification', '') or ''}".strip("|")
                    candidate_spec = candidate.get("specification", "") or ""
                    # 仕様が一致し、まだ使用されていない項目を探す
                    if candidate_spec == ai_spec and candidate_key not in used_human_keys:
                        human_item = candidate
                        used_human_keys.add(candidate_key)
                        break

            # 3. 部分マッチも試行（名前+仕様の部分一致）
            if not human_item:
                for h_key, h_item in human_index.items():
                    h_name = h_item.get("name", "")
                    h_spec = h_item.get("specification", "") or ""
                    if h_key not in used_human_keys:
                        # 名前が部分一致し、仕様も部分一致する場合
                        if (item_name in h_name or h_name in item_name) and (ai_spec in h_spec or h_spec in ai_spec or not ai_spec or not h_spec):
                            human_item = h_item
                            used_human_keys.add(h_key)
                            break

            result = self.verify_item(ai_item, human_item, spec_extraction)
            results.append(result)

        # 統計を計算
        total_ai = sum(r.ai_amount for r in results)
        total_human = sum(r.human_amount or 0 for r in results)
        matched_count = sum(1 for r in results if r.match_status in ["一致", "許容範囲"])
        issue_count = sum(len(r.issues) for r in results)

        # レポートを生成
        report = {
            "summary": {
                "total_items": len(results),
                "matched_items": matched_count,
                "match_rate": matched_count / len(results) if results else 0,
                "ai_total": total_ai,
                "human_total": total_human,
                "total_difference": total_ai - total_human if total_human else None,
                "total_difference_ratio": (total_ai - total_human) / total_human if total_human else None,
                "issue_count": issue_count,
            },
            "spec_extraction": {
                "building_area_m2": spec_extraction.building_area_m2 if spec_extraction else None,
                "building_floors": spec_extraction.building_floors if spec_extraction else None,
                "room_count": spec_extraction.room_count if spec_extraction else None,
                "required_equipment": spec_extraction.required_equipment if spec_extraction else [],
            },
            "items": [
                {
                    "item_name": r.item_name,
                    "ai_amount": r.ai_amount,
                    "human_amount": r.human_amount,
                    "difference": r.difference,
                    "difference_ratio": r.difference_ratio,
                    "match_status": r.match_status,
                    "calculation_basis": r.calculation_trace.calculation_basis.value,
                    "calculation_formula": r.calculation_trace.calculation_formula,
                    "kb_reference": r.calculation_trace.kb_reference,
                    "confidence": r.calculation_trace.confidence,
                    "notes": r.calculation_trace.notes,
                    "issues": r.issues,
                }
                for r in results
            ],
            "issues_summary": [
                {"item": r.item_name, "issues": r.issues}
                for r in results if r.issues
            ]
        }

        return report

    def format_report_text(self, report: Dict) -> str:
        """レポートをテキスト形式でフォーマット"""
        lines = []

        lines.append("=" * 70)
        lines.append("見積算出ロジック検証レポート")
        lines.append("=" * 70)

        # サマリー
        s = report["summary"]
        lines.append("\n【サマリー】")
        lines.append(f"  総項目数: {s['total_items']}")
        lines.append(f"  マッチ率: {s['match_rate']:.1%} ({s['matched_items']}/{s['total_items']})")
        lines.append(f"  AI生成合計: ¥{s['ai_total']:,.0f}")
        if s['human_total']:
            lines.append(f"  人間作成合計: ¥{s['human_total']:,.0f}")
            lines.append(f"  差額: ¥{s['total_difference']:+,.0f} ({s['total_difference_ratio']:+.1%})")
        lines.append(f"  検出問題数: {s['issue_count']}")

        # 仕様書抽出情報
        spec = report["spec_extraction"]
        if spec["building_area_m2"] or spec["room_count"]:
            lines.append("\n【仕様書から抽出した情報】")
            if spec["building_area_m2"]:
                lines.append(f"  延床面積: {spec['building_area_m2']:,.0f}㎡")
            if spec["building_floors"]:
                lines.append(f"  階数: {spec['building_floors']}階")
            if spec["room_count"]:
                lines.append(f"  推定部屋数: {spec['room_count']}室")
            if spec["required_equipment"]:
                lines.append(f"  必要設備: {', '.join(spec['required_equipment'])}")

        # 項目詳細
        lines.append("\n【項目別算出根拠】")
        lines.append("-" * 70)

        for item in report["items"]:
            lines.append(f"\n■ {item['item_name']}")
            lines.append(f"  AI金額: ¥{item['ai_amount']:,.0f}")
            if item['human_amount']:
                lines.append(f"  参照金額: ¥{item['human_amount']:,.0f} ({item['match_status']})")
            lines.append(f"  算出根拠: {item['calculation_basis']}")
            if item['calculation_formula']:
                lines.append(f"  計算式: {item['calculation_formula']}")
            if item['kb_reference']:
                lines.append(f"  KB参照: {item['kb_reference']}")
            if item['notes']:
                lines.append(f"  備考: {item['notes']}")
            lines.append(f"  信頼度: {item['confidence']:.0%}")
            if item['issues']:
                lines.append(f"  ⚠️ 問題: {', '.join(item['issues'])}")

        # 問題サマリー
        if report["issues_summary"]:
            lines.append("\n" + "=" * 70)
            lines.append("【要確認項目】")
            lines.append("=" * 70)
            for issue in report["issues_summary"]:
                lines.append(f"  • {issue['item']}: {', '.join(issue['issues'])}")

        return "\n".join(lines)


if __name__ == "__main__":
    # テスト実行
    verifier = EstimateVerifier()

    # テストデータ
    ai_items = [
        {"name": "白ガス管（ネジ接合）", "quantity": 93, "unit": "m", "unit_price": 8990, "amount": 836070},
        {"name": "LED照明", "quantity": 10, "unit": "灯", "unit_price": 20700, "amount": 207000},
        {"name": "コンセント", "quantity": 50, "unit": "個", "unit_price": 3500, "amount": 175000},
    ]

    human_items = [
        {"name": "白ガス管（ネジ接合）", "amount": 836070},
        {"name": "LED照明", "amount": 200000},
    ]

    spec_text = """
    都立山崎高等学校仮設校舎
    延床面積: 2,145㎡
    3階建
    設備: キュービクル、分電盤、LED照明、LAN、放送設備
    """

    report = verifier.generate_verification_report(ai_items, human_items, spec_text)
    print(verifier.format_report_text(report))
