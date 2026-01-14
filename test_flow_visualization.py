#!/usr/bin/env python3
"""
処理フロー可視化テストスクリプト

実際の仕様書PDFを使用して、AI見積生成の各ステップを
詳細に可視化・ログ出力します。
"""

import json
import time
from pathlib import Path
from datetime import datetime
from pipelines.estimate_generator_ai import AIEstimateGenerator
from pipelines.schemas import DisciplineType

# 出力色（ターミナル用）
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_header(title: str):
    """セクションヘッダーを出力"""
    print(f"\n{Colors.HEADER}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}  {title}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*70}{Colors.ENDC}\n")

def print_step(step_num: int, title: str, duration: float = None):
    """ステップヘッダーを出力"""
    time_str = f" ({duration:.2f}秒)" if duration else ""
    print(f"\n{Colors.CYAN}【Step {step_num}】{title}{time_str}{Colors.ENDC}")
    print(f"{Colors.CYAN}{'-'*50}{Colors.ENDC}")

def print_info(label: str, value: str):
    """情報行を出力"""
    print(f"  {Colors.GREEN}✓{Colors.ENDC} {label}: {Colors.BOLD}{value}{Colors.ENDC}")

def print_warning(message: str):
    """警告を出力"""
    print(f"  {Colors.YELLOW}⚠{Colors.ENDC} {message}")

def print_error(message: str):
    """エラーを出力"""
    print(f"  {Colors.RED}✗{Colors.ENDC} {message}")

def print_table(headers: list, rows: list, max_rows: int = 20):
    """テーブル形式で出力"""
    # カラム幅を計算
    col_widths = [len(h) for h in headers]
    for row in rows[:max_rows]:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)[:40]))

    # ヘッダー
    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    print(f"  {header_line}")
    print(f"  {'-' * len(header_line)}")

    # 行
    for row in rows[:max_rows]:
        row_line = " | ".join(str(cell)[:40].ljust(col_widths[i]) for i, cell in enumerate(row))
        print(f"  {row_line}")

    if len(rows) > max_rows:
        print(f"  ... 他 {len(rows) - max_rows} 件")


def run_visualization_test():
    """処理フロー可視化テストを実行"""

    # 仕様書PDF
    spec_pdf = Path("test-files/仕様書【都立山崎高等学校仮設校舎等の借入れ】ord202403101060100130187c1e4d0.pdf")

    if not spec_pdf.exists():
        print_error(f"仕様書PDFが見つかりません: {spec_pdf}")
        return

    print_header("積算AI 処理フロー可視化テスト")
    print_info("仕様書", spec_pdf.name)
    print_info("実行日時", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # ジェネレータ初期化
    start_init = time.time()
    generator = AIEstimateGenerator()
    init_time = time.time() - start_init

    print_step(0, "初期化", init_time)
    print_info("KB項目数", f"{len(generator.price_kb)}件")

    # KB統計
    kb_by_discipline = {}
    for item in generator.price_kb:
        disc = item.get("discipline", "不明")
        kb_by_discipline[disc] = kb_by_discipline.get(disc, 0) + 1

    print("\n  【KB工事区分別】")
    for disc, count in sorted(kb_by_discipline.items(), key=lambda x: -x[1]):
        print(f"    {disc}: {count}件")

    # ===== Step 1: テキスト抽出 =====
    print_step(1, "テキスト抽出（PyPDF2 → OCRフォールバック）")
    start_step = time.time()

    with open(spec_pdf, "rb") as f:
        pdf_content = f.read()

    text = generator.extract_text_from_pdf(pdf_content)

    # テキストが取れない場合はOCRを使用
    if len(text.strip()) < 100:
        print_warning("PyPDF2でテキスト抽出失敗、OCRにフォールバック...")
        try:
            from pipelines.ocr_extractor import OCRExtractor
            ocr = OCRExtractor()
            ocr_result = ocr.extract_from_pdf(pdf_content, max_pages=10)
            text = ocr_result.get("text", "")
            print_info("OCR使用", "Vision API")
        except Exception as e:
            print_warning(f"OCRも失敗: {e}")
            # デモ用に仮のテキストを使用
            text = """都立山崎高等学校 仮設校舎等の借入れ
            工事場所: 東京都町田市山崎町1453番地1
            延床面積: 約2,145㎡
            階数: 地上2階
            用途: 学校（仮設校舎）
            ガス設備: 都市ガス13A
            リース期間: 令和6年4月〜令和9年3月（3年間）"""
            print_warning("デモ用テキストを使用")

    step1_time = time.time() - start_step

    print_info("抽出文字数", f"{len(text):,}文字")
    print_info("処理時間", f"{step1_time:.2f}秒")

    # 抽出テキストのサンプル
    print("\n  【抽出テキストサンプル（先頭500文字）】")
    sample = text[:500].replace('\n', ' ')
    print(f"  {sample}...")

    # ===== Step 2: 建物情報抽出（LLM） =====
    print_step(2, "建物情報抽出（Claude API）")
    start_step = time.time()

    building_info = generator.extract_building_info(text)
    step2_time = time.time() - start_step

    print_info("処理時間", f"{step2_time:.2f}秒")

    if building_info:
        print("\n  【抽出された建物情報】")
        for key, value in building_info.items():
            if value:
                print(f"    {key}: {value}")

    # ===== Step 3: 諸元表抽出（Vision API） =====
    print_step(3, "諸元表抽出（Vision API）")
    start_step = time.time()

    try:
        spec_table = generator.extract_specification_table_with_vision(pdf_content)
        step3_time = time.time() - start_step

        print_info("処理時間", f"{step3_time:.2f}秒")

        if spec_table:
            print_info("部屋タイプ数", f"{len(spec_table.get('room_types', []))}種類")
            total_gas = sum(r.get("gas_outlets", 0) for r in spec_table.get("room_types", []))
            total_outlets = sum(r.get("outlets", 0) for r in spec_table.get("room_types", []))
            print_info("ガス栓総数", f"{total_gas}箇所")
            print_info("コンセント総数", f"{total_outlets}個")
        else:
            print_warning("諸元表の抽出に失敗")
    except Exception as e:
        print_warning(f"Vision API処理スキップ: {e}")
        spec_table = None
        step3_time = 0

    # ===== Step 4: 詳細項目生成（LLM + KB語彙） =====
    print_step(4, "詳細項目生成（Claude API + KB語彙リスト）")
    start_step = time.time()

    # ガス設備工事で生成
    discipline = DisciplineType.GAS

    # プロンプトに含まれるKB語彙リストを表示
    print("\n  【プロンプトに含まれるKB語彙リスト（一部）】")
    gas_kb_items = [item for item in generator.price_kb if "ガス" in item.get("discipline", "")]
    sample_kb_names = list(set([item.get("description", "")[:20] for item in gas_kb_items]))[:10]
    for name in sample_kb_names:
        print(f"    - {name}")
    print(f"    ... 他 {len(gas_kb_items) - 10} 件")

    # 生成実行
    print("\n  【生成中...】")
    # メソッドはbuilding_infoのみを受け取る
    items = generator.generate_detailed_items_for_gas(building_info)
    step4_time = time.time() - start_step

    print_info("生成項目数", f"{len(items)}件")
    print_info("処理時間", f"{step4_time:.2f}秒")

    # EstimateItemオブジェクトかdictかを判定して統一的に処理
    def get_item_attr(item, attr, default=""):
        if hasattr(item, attr):
            return getattr(item, attr, default)
        elif isinstance(item, dict):
            return item.get(attr, default)
        return default

    # 生成項目の内訳
    level_counts = {}
    for item in items:
        lvl = get_item_attr(item, "level", 0)
        level_counts[lvl] = level_counts.get(lvl, 0) + 1

    print("\n  【階層別項目数】")
    for lvl in sorted(level_counts.keys()):
        print(f"    Level {lvl}: {level_counts[lvl]}件")

    # 生成項目サンプル
    print("\n  【生成項目サンプル（先頭10件）】")
    headers = ["Level", "項目名", "仕様", "数量", "単位"]
    rows = []
    for item in items[:10]:
        rows.append([
            get_item_attr(item, "level", 0),
            str(get_item_attr(item, "name", ""))[:25],
            str(get_item_attr(item, "specification", "") or "")[:15],
            get_item_attr(item, "quantity", ""),
            get_item_attr(item, "unit", "")
        ])
    print_table(headers, rows)

    # ===== Step 5: チェックリスト検証 =====
    print_step(5, "チェックリスト検証")

    from pipelines.estimation_rules import DISCIPLINE_CHECKLISTS

    checklist = DISCIPLINE_CHECKLISTS.get(discipline.value, {})
    total_checklist_items = sum(len(items_list) for items_list in checklist.values())

    print_info("チェックリスト項目数", f"{total_checklist_items}件")

    # カバレッジ計算（簡易版）
    generated_names = [get_item_attr(item, "name", "") for item in items]
    all_checklist_items = []
    for category, check_items in checklist.items():
        all_checklist_items.extend(check_items)

    matched_count = 0
    missing_items = []
    for check_item in all_checklist_items:
        if any(check_item in name or name in check_item for name in generated_names):
            matched_count += 1
        else:
            missing_items.append(check_item)

    coverage = matched_count / len(all_checklist_items) if all_checklist_items else 0

    print_info("カバー率", f"{coverage:.1%}")
    print_info("マッチ項目数", f"{matched_count}/{len(all_checklist_items)}")

    if missing_items:
        print("\n  【不足項目（一部）】")
        for item in missing_items[:5]:
            print(f"    - {item}")
        if len(missing_items) > 5:
            print(f"    ... 他 {len(missing_items) - 5} 件")

    # coverage_resultを後続のために作成
    coverage_result = {
        'coverage': coverage,
        'matched_count': matched_count,
        'total_count': len(all_checklist_items),
        'missing_items': missing_items
    }

    # ===== Step 6: KBマッチング（単価付与） =====
    print_step(6, "KBマッチング（ベクトル検索 + 文字列マッチング）")
    start_step = time.time()

    # itemsがすでにEstimateItemの場合はそのまま使用、dictの場合は変換
    from pipelines.schemas import EstimateItem
    estimate_items = []
    for item in items:
        if isinstance(item, EstimateItem):
            estimate_items.append(item)
        else:
            try:
                est_item = EstimateItem(
                    item_no=item.get("item_no", ""),
                    level=item.get("level", 0),
                    name=item.get("name", ""),
                    specification=item.get("specification"),
                    quantity=item.get("quantity"),
                    unit=item.get("unit", ""),
                    discipline=discipline,
                )
                estimate_items.append(est_item)
            except Exception as e:
                print_warning(f"項目変換エラー: {item.get('name', '')}: {e}")

    # マッチング実行
    enriched_items = generator.enrich_with_prices(estimate_items)
    step6_time = time.time() - start_step

    print_info("処理時間", f"{step6_time:.2f}秒")

    # マッチング結果集計
    matched_count = 0
    unmatched = []
    matched_details = []

    for item in enriched_items:
        if item.level == 0:
            continue
        if item.unit_price and item.unit_price > 0:
            matched_count += 1
            matched_details.append({
                "name": item.name,
                "spec": item.specification or "",
                "unit_price": item.unit_price,
                "source": item.source_reference or ""
            })
        else:
            unmatched.append(item.name)

    total_items = len([i for i in enriched_items if i.level > 0])
    match_rate = matched_count / total_items if total_items > 0 else 0

    print_info("マッチング率", f"{match_rate:.1%} ({matched_count}/{total_items}件)")

    # マッチング成功例
    print("\n  【マッチング成功例（先頭10件）】")
    headers = ["項目名", "仕様", "単価", "根拠"]
    rows = []
    for detail in matched_details[:10]:
        rows.append([
            detail["name"][:20],
            detail["spec"][:10],
            f"¥{detail['unit_price']:,.0f}",
            detail["source"][:25]
        ])
    print_table(headers, rows)

    # マッチング失敗例
    if unmatched:
        print(f"\n  【未マッチ項目 ({len(unmatched)}件)】")
        for name in unmatched[:10]:
            print(f"    {Colors.RED}✗{Colors.ENDC} {name}")
        if len(unmatched) > 10:
            print(f"    ... 他 {len(unmatched) - 10} 件")

    # ===== Step 7: 金額計算 =====
    print_step(7, "金額計算（子→親の逆順処理）")

    # 金額計算
    total_amount = 0
    for item in enriched_items:
        if item.level == 0:
            continue
        if item.amount and item.amount > 0:
            total_amount += item.amount
        elif item.unit_price and item.quantity:
            item.amount = item.unit_price * item.quantity
            total_amount += item.amount

    print_info("推定総額", f"¥{total_amount:,.0f}")

    # 金額内訳（カテゴリ別）
    category_amounts = {}
    for item in enriched_items:
        if item.level == 1 and item.name:
            category_amounts[item.name] = 0
            # 子項目の合計を計算
            for child in enriched_items:
                if child.level == 2 and child.amount:
                    # 簡易的に親子関係を推定（同じカテゴリキーワードを含む）
                    if any(word in child.name for word in item.name.split()[:2] if len(word) > 1):
                        category_amounts[item.name] += child.amount

    if category_amounts:
        print("\n  【カテゴリ別金額】")
        for cat, amount in sorted(category_amounts.items(), key=lambda x: -x[1]):
            if amount > 0:
                print(f"    {cat}: ¥{amount:,.0f}")

    # ===== 最終サマリー =====
    print_header("処理サマリー")

    total_time = init_time + step1_time + step2_time + step3_time + step4_time + step6_time

    print_info("総処理時間", f"{total_time:.2f}秒")
    print("\n  【ステップ別処理時間】")
    print(f"    初期化:         {init_time:.2f}秒")
    print(f"    テキスト抽出:   {step1_time:.2f}秒")
    print(f"    建物情報抽出:   {step2_time:.2f}秒")
    print(f"    諸元表抽出:     {step3_time:.2f}秒")
    print(f"    詳細項目生成:   {step4_time:.2f}秒")
    print(f"    KBマッチング:   {step6_time:.2f}秒")

    print("\n  【精度指標】")
    print(f"    生成項目数:     {len(items)}件")
    print(f"    マッチング率:   {match_rate:.1%}")
    print(f"    チェックリストカバー率: {coverage_result['coverage']:.1%}")
    print(f"    推定総額:       ¥{total_amount:,.0f}")

    # 結果をJSONに保存
    result = {
        "execution_time": datetime.now().isoformat(),
        "spec_pdf": spec_pdf.name,
        "total_processing_time": total_time,
        "step_times": {
            "init": init_time,
            "text_extraction": step1_time,
            "building_info": step2_time,
            "spec_table": step3_time,
            "item_generation": step4_time,
            "kb_matching": step6_time
        },
        "metrics": {
            "generated_items": len(items),
            "matching_rate": match_rate,
            "matched_count": matched_count,
            "unmatched_count": len(unmatched),
            "checklist_coverage": coverage_result['coverage'],
            "total_amount": total_amount
        },
        "unmatched_items": unmatched,
        "kb_stats": kb_by_discipline
    }

    output_path = Path("output/flow_visualization_result.json")
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n  結果保存: {output_path}")

    print(f"\n{Colors.GREEN}{'='*70}{Colors.ENDC}")
    print(f"{Colors.GREEN}  処理完了{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*70}{Colors.ENDC}\n")


if __name__ == "__main__":
    run_visualization_test()
