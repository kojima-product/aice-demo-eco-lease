"""
法令データベース管理

関係法令PDFをアップロードして、法令情報のデータベースを構築・管理します。
"""

import streamlit as st
from pathlib import Path
import tempfile
import json
from datetime import datetime
from loguru import logger
import sys
import os

sys.path.insert(0, '.')

from pipelines.schemas import LegalReference, DisciplineType


# ページ設定
st.set_page_config(
    page_title="法令データベース",
    page_icon="page_facing_up",
    layout="wide",
    initial_sidebar_state="expanded"
)

# カスタムCSS
st.markdown("""
<style>
    /* サイドバーから「app」を非表示 */
    [data-testid="stSidebarNav"] > ul > li:first-child {
        display: none;
    }
    /* サイドバー幅を拡大 */
    [data-testid="stSidebar"] {
        min-width: 300px;
        max-width: 360px;
    }
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 1.5rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
    }

    /* メインコンテナ */
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
        max-width: 1400px;
    }

    /* メトリクスカード */
    [data-testid="stMetricValue"] {
        font-size: 1.4rem;
        font-weight: 600;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.85rem;
    }

    /* タブスタイル */
    .stTabs [data-baseweb="tab"] {
        padding: 12px 24px;
        font-weight: 500;
        font-size: 0.95rem;
    }

    /* セクションヘッダー */
    .sidebar-section-header {
        font-size: 0.9rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
        padding-bottom: 0.3rem;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    }
</style>
""", unsafe_allow_html=True)


# 法令KB用のクラス
class LegalKBBuilder:
    """法令PDFから法令情報を抽出してKB化"""

    def __init__(self, kb_path: str = "kb/legal_kb.json"):
        from dotenv import load_dotenv
        from anthropic import Anthropic

        load_dotenv()
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self.kb_path = kb_path
        self.kb_items = []

        # 既存KBを読み込み
        if Path(kb_path).exists():
            with open(kb_path, 'r', encoding='utf-8') as f:
                self.kb_items = json.load(f)
            logger.info(f"Loaded {len(self.kb_items)} items from Legal KB")

    def extract_legal_from_pdf(self, pdf_path: str, source_name: str = None) -> list:
        """法令PDFから法令情報を抽出"""
        import PyPDF2

        logger.info(f"Extracting legal info from: {pdf_path}")

        if source_name is None:
            source_name = Path(pdf_path).stem

        # PDFからテキストを抽出
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            text = ""
            for page_num in range(min(len(pdf_reader.pages), 30)):
                text += pdf_reader.pages[page_num].extract_text() + "\n"

        logger.info(f"Extracted {len(text)} characters from PDF")

        # テキストがほとんど抽出できない場合はOCRを使用
        if len(text.strip()) < 500:
            logger.warning("Text extraction failed, using OCR...")
            try:
                from pipelines.ocr_extractor import OCRExtractor
                ocr = OCRExtractor()

                # OCRでテキスト抽出（法令用に全ページテキスト連結）
                items_data = ocr.extract_from_pdf(pdf_path)
                text = "\n".join([
                    f"{item.get('name', '')} {item.get('specification', '')}"
                    for item in items_data
                ])
                logger.info(f"OCR extraction completed: {len(text)} characters")
            except Exception as e:
                logger.error(f"OCR failed: {e}")
                return []

        # LLMで構造化データに変換
        prompt = f"""以下の法令・基準一覧PDFから、法令情報を抽出してください。

法令文書テキスト:
{text[:60000]}

【抽出する情報】
各法令・基準について、以下の情報を正確に抽出してください：

1. **law_code**: 法令コード（例: JEAC8001, 建基法, 消防法）
2. **law_name**: 法令名（正式名称）
3. **category**: カテゴリ
   - "共通": 全工事区分に適用
   - "電気": 電気設備工事関連
   - "機械": 機械設備工事関連
   - "管工事": 給排水・空調関連
   - "ガス": ガス設備工事関連
   - "消防": 消防設備関連
4. **year**: 制定年または改正年（わかる場合）
5. **description**: 概要説明（100文字程度）
6. **key_points**: 重要ポイント（配列形式）
7. **applicable_items**: 適用される見積項目（例: ["分電盤", "配線", "接地"]）
8. **relevance_score**: 見積作成における重要度（0.0-1.0）
   - 1.0: 必須遵守（技術基準等）
   - 0.8: 高重要度
   - 0.5: 参照推奨
   - 0.3: 参考程度

【重要な注意事項】
- 赤字や太字で強調されている法令は特に重要（relevance_score高め）
- 建築・設備工事に関係する法令を優先
- 法令番号やJIS番号も抽出

【出力形式】
JSON配列形式で出力してください：

```json
[
  {{
    "law_code": "JEAC8001",
    "law_name": "内線規程",
    "category": "電気",
    "year": 2022,
    "description": "低圧屋内配線の設計・施工に関する技術基準",
    "key_points": [
      "低圧屋内配線の保護",
      "接地工事の基準",
      "過電流保護装置の設置"
    ],
    "applicable_items": ["分電盤", "配線", "接地", "コンセント"],
    "relevance_score": 1.0
  }},
  {{
    "law_code": "建基法",
    "law_name": "建築基準法",
    "category": "共通",
    "year": 2024,
    "description": "建築物の安全性、衛生等に関する基準を定める法律",
    "key_points": [
      "構造安全性",
      "防火区画",
      "避難経路"
    ],
    "applicable_items": ["防火区画貫通", "配管スリーブ"],
    "relevance_score": 0.9
  }}
]
```

必ずJSON形式で回答してください。"""

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=16000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )

            # コスト記録
            from pipelines.cost_tracker import record_cost
            record_cost(
                operation="法令KB抽出",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={"file": source_name}
            )

            response_text = response.content[0].text

            # JSONを抽出
            json_start = response_text.find('[')
            json_end = response_text.rfind(']') + 1

            if json_start == -1 or json_end == 0:
                logger.error("No JSON found in response")
                return []

            json_str = response_text[json_start:json_end]
            items_data = json.loads(json_str)

            logger.info(f"Extracted {len(items_data)} legal items")

            # source情報を追加
            for item in items_data:
                item['source_file'] = source_name
                item['extracted_at'] = datetime.now().isoformat()

            return items_data

        except Exception as e:
            logger.error(f"Error extracting legal info: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def save_kb(self, items: list):
        """KBを保存"""
        # ディレクトリ作成
        Path(self.kb_path).parent.mkdir(parents=True, exist_ok=True)

        with open(self.kb_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

        self.kb_items = items
        logger.info(f"Saved {len(items)} legal items to {self.kb_path}")

    def merge_with_existing(self, new_items: list, strategy: str = "keep_new") -> list:
        """既存KBとマージ"""
        existing_map = {item.get('law_code', ''): item for item in self.kb_items}

        merged = []
        added = 0
        updated = 0

        for new_item in new_items:
            law_code = new_item.get('law_code', '')

            if law_code in existing_map:
                if strategy == "keep_new":
                    merged.append(new_item)
                elif strategy == "keep_old":
                    merged.append(existing_map[law_code])
                updated += 1
                del existing_map[law_code]
            else:
                merged.append(new_item)
                added += 1

        # 残った既存項目を追加
        merged.extend(existing_map.values())

        logger.info(f"Merge complete: {added} added, {updated} updated, {len(merged)} total")
        return merged


def init_session_state():
    """セッション状態を初期化"""
    if 'legal_kb_builder' not in st.session_state:
        st.session_state.legal_kb_builder = LegalKBBuilder()
    if 'extracted_legal_items' not in st.session_state:
        st.session_state.extracted_legal_items = []


def display_legal_kb_stats():
    """法令KB統計情報を表示"""
    kb_items = st.session_state.legal_kb_builder.kb_items

    if not kb_items:
        st.info("法令データベースにデータがありません。「法令アップロード」タブからPDFをアップロードしてください。")
        return

    # カテゴリ別統計
    category_stats = {}
    for item in kb_items:
        category = item.get('category', '不明')
        if category not in category_stats:
            category_stats[category] = {'count': 0, 'high_relevance': 0}
        category_stats[category]['count'] += 1
        if item.get('relevance_score', 0) >= 0.8:
            category_stats[category]['high_relevance'] += 1

    # メトリクス表示
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "登録法令数",
            f"{len(kb_items)}件",
            help="データベースに登録されている法令・基準の総数"
        )

    with col2:
        st.metric(
            "カテゴリ数",
            f"{len(category_stats)}種類",
            help="登録されているカテゴリの種類（共通・電気・ガス等）"
        )

    with col3:
        high_rel = sum(1 for item in kb_items if item.get('relevance_score', 0) >= 0.8)
        st.metric(
            "高重要度",
            f"{high_rel}件",
            help="重要度スコア0.8以上の法令数"
        )

    # カテゴリ別詳細
    st.divider()
    st.markdown("**カテゴリ別統計**")

    for category, stats in sorted(category_stats.items()):
        with st.expander(f"{category} ({stats['count']}件)"):
            col1, col2 = st.columns(2)
            with col1:
                st.metric("法令数", f"{stats['count']}件")
            with col2:
                st.metric("高重要度", f"{stats['high_relevance']}件")


def main():
    init_session_state()

    # ヘッダー
    st.title("法令データベース")
    st.caption("関係法令・基準の情報を管理")

    # サイドバー
    with st.sidebar:
        st.markdown("### データベース概要")

        kb_items = st.session_state.legal_kb_builder.kb_items

        st.markdown('<p class="sidebar-section-header">登録状況</p>', unsafe_allow_html=True)

        if kb_items:
            col1, col2 = st.columns(2)
            with col1:
                st.metric(
                    "法令数",
                    f"{len(kb_items):,}",
                    help="登録されている法令・基準の数"
                )
            with col2:
                categories = set(item.get('category', '') for item in kb_items)
                st.metric(
                    "カテゴリ",
                    f"{len(categories)}種類",
                    help="登録されているカテゴリの種類"
                )

            # カテゴリ別内訳
            st.markdown('<p class="sidebar-section-header">カテゴリ別内訳</p>', unsafe_allow_html=True)
            category_counts = {}
            for item in kb_items:
                c = item.get('category', '不明')
                category_counts[c] = category_counts.get(c, 0) + 1

            for category, count in sorted(category_counts.items()):
                st.text(f"{category}: {count}件")
        else:
            st.warning("データベースは空です")
            st.caption("「法令アップロード」タブから関係法令PDFをアップロードしてください")

        st.markdown("---")

        st.markdown('<p class="sidebar-section-header">対応形式</p>', unsafe_allow_html=True)
        st.markdown("""
        | 形式 | 対応 |
        |------|------|
        | PDF | 対応（OCR対応） |
        """)

        st.markdown("---")
        st.caption("見積生成デモ v2.0")

    # タブ
    tab1, tab2, tab3 = st.tabs(["法令アップロード", "データ管理", "使い方"])

    # タブ1: アップロード
    with tab1:
        st.info("""
**法令データベースとは**

関係法令・基準（建築基準法、JEAC8001、ガス事業法等）のタイトルと概要を登録したデータベースです。

1. 法令一覧PDF（`関係法令一覧.pdf`など）をアップロード
2. AIが法令名・カテゴリ・重要度を自動抽出
3. データベースに登録

登録したデータは、見積書作成時の法令遵守チェックに使用されます。
        """)

        col1, col2 = st.columns([2, 1])

        with col1:
            st.markdown("**法令PDFファイル**")

            uploaded_files = st.file_uploader(
                "法令PDFを選択（複数可）",
                type=['pdf'],
                accept_multiple_files=True,
                help="関係法令一覧などのPDFをアップロードしてください",
                label_visibility="collapsed"
            )

        with col2:
            st.markdown("**設定**")

            merge_options = {
                "新データで上書き": "keep_new",
                "既存データを保持": "keep_old"
            }
            merge_label = st.selectbox(
                "重複時の処理",
                list(merge_options.keys()),
                index=0,
                help="同じ法令コードが既にある場合の処理"
            )
            merge_strategy = merge_options[merge_label]

        st.divider()

        if uploaded_files:
            st.success(f"選択済み: {len(uploaded_files)}ファイル")

            with st.expander("選択ファイル一覧", expanded=False):
                for file in uploaded_files:
                    file_size = len(file.getbuffer()) / 1024
                    st.text(f"[PDF] {file.name} ({file_size:.1f} KB)")

            col1, col2 = st.columns(2)

            with col1:
                if st.button("法令抽出開始", type="primary", use_container_width=True):
                    st.markdown("---")
                    st.subheader("処理状況")

                    all_extracted = []
                    progress_bar = st.progress(0)
                    status_text = st.empty()

                    for idx, uploaded_file in enumerate(uploaded_files):
                        progress = idx / len(uploaded_files)
                        progress_bar.progress(progress)
                        status_text.markdown(f"### 処理中: {uploaded_file.name}")

                        # 一時ファイルに保存
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                            tmp_file.write(uploaded_file.getbuffer())
                            tmp_path = tmp_file.name

                        try:
                            kb_builder = st.session_state.legal_kb_builder
                            items = kb_builder.extract_legal_from_pdf(
                                tmp_path,
                                source_name=uploaded_file.name
                            )

                            if items:
                                all_extracted.extend(items)
                                st.success(f"{uploaded_file.name}: {len(items)}件の法令を抽出")
                            else:
                                st.error(f"{uploaded_file.name}: 抽出失敗")

                        except Exception as e:
                            st.error(f"エラー ({uploaded_file.name}): {str(e)}")

                        finally:
                            try:
                                os.unlink(tmp_path)
                            except:
                                pass

                    progress_bar.progress(1.0)
                    status_text.markdown("### 処理完了")

                    if all_extracted:
                        st.session_state.extracted_legal_items = all_extracted
                        st.success(f"合計 {len(all_extracted)}件の法令を抽出しました")

                        # サンプル表示
                        with st.expander("抽出サンプル（最初の5件）"):
                            for item in all_extracted[:5]:
                                st.markdown(f"""
                                **{item.get('law_name', '')}** (`{item.get('law_code', '')}`)
                                - カテゴリ: {item.get('category', '')}
                                - 重要度: {item.get('relevance_score', 0):.1f}
                                - 概要: {item.get('description', '')[:100]}...
                                """)

            with col2:
                if st.button("法令KBに保存", use_container_width=True,
                            disabled=not st.session_state.extracted_legal_items):
                    st.markdown("---")
                    st.subheader("保存処理")

                    with st.spinner("マージ中..."):
                        kb_builder = st.session_state.legal_kb_builder

                        merged = kb_builder.merge_with_existing(
                            st.session_state.extracted_legal_items,
                            strategy=merge_strategy
                        )

                        kb_builder.save_kb(merged)

                        st.success(f"法令KBを保存しました: {len(merged)}件")
                        st.info(f"保存先: {kb_builder.kb_path}")

                        st.session_state.extracted_legal_items = []
                        st.rerun()
        else:
            st.info("PDF形式のファイルをアップロードしてください")

    # タブ2: データ管理
    with tab2:
        display_legal_kb_stats()

        st.divider()

        kb_items = st.session_state.legal_kb_builder.kb_items

        if kb_items:
            st.markdown("**法令詳細**")

            # フィルタリング
            col1, col2, col3 = st.columns(3)

            with col1:
                categories = list(set(item.get('category', '不明') for item in kb_items))
                selected_category = st.selectbox(
                    "カテゴリフィルタ",
                    ["すべて"] + sorted(categories)
                )

            with col2:
                search_query = st.text_input("法令名で検索", "")

            with col3:
                display_limit = st.number_input("表示件数", min_value=10, max_value=100, value=20)

            # フィルタリング適用
            filtered_items = kb_items

            if selected_category != "すべて":
                filtered_items = [item for item in filtered_items
                                 if item.get('category') == selected_category]

            if search_query:
                filtered_items = [item for item in filtered_items
                                 if search_query.lower() in item.get('law_name', '').lower()
                                 or search_query.lower() in item.get('law_code', '').lower()]

            st.info(f"{len(filtered_items)}件（全{len(kb_items)}件中）")

            # 一覧表示
            for idx, item in enumerate(filtered_items[:display_limit], 1):
                relevance = item.get('relevance_score', 0)
                relevance_label = "高" if relevance >= 0.8 else ("中" if relevance >= 0.5 else "低")

                with st.expander(
                    f"{idx}. {item.get('law_name', '')} [{item.get('law_code', '')}] - 重要度: {relevance_label}"
                ):
                    col1, col2 = st.columns(2)

                    with col1:
                        st.markdown(f"**カテゴリ**: {item.get('category', '')}")
                        st.markdown(f"**制定年**: {item.get('year', '不明')}")
                        st.markdown(f"**重要度スコア**: {relevance:.1f}")
                        st.markdown(f"**概要**: {item.get('description', '')}")

                    with col2:
                        key_points = item.get('key_points', [])
                        if key_points:
                            st.markdown("**重要ポイント**:")
                            for point in key_points:
                                st.markdown(f"- {point}")

                        applicable = item.get('applicable_items', [])
                        if applicable:
                            st.markdown(f"**適用項目**: {', '.join(applicable)}")

            # エクスポート
            st.markdown("---")
            col1, col2 = st.columns(2)

            with col1:
                if st.button("JSON出力", use_container_width=True):
                    kb_json = json.dumps(kb_items, ensure_ascii=False, indent=2)
                    st.download_button(
                        label="法令KBをダウンロード",
                        data=kb_json,
                        file_name=f"legal_kb_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        mime="application/json"
                    )

            with col2:
                if st.button("法令KBをクリア", use_container_width=True, type="secondary"):
                    if st.checkbox("本当にクリアしますか？", key="confirm_clear"):
                        st.session_state.legal_kb_builder.kb_items = []
                        st.session_state.legal_kb_builder.save_kb([])
                        st.success("法令KBをクリアしました")
                        st.rerun()

    # タブ3: 使い方
    with tab3:
        st.markdown("""
        ## 法令データベースとは

        見積作成時に参照すべき関係法令・基準の情報を管理するデータベースです。

        ### 登録される情報

        | 項目 | 説明 | 例 |
        |------|------|-----|
        | 法令コード | 識別コード | JEAC8001, 建基法 |
        | 法令名 | 正式名称 | 内線規程、建築基準法 |
        | カテゴリ | 適用分野 | 共通, 電気, ガス |
        | 重要度 | 見積作成での重要性 | 1.0（必須）〜0.3（参考） |
        | 概要 | 法令の概要説明 | - |
        | 重要ポイント | 特に注意すべき点 | - |
        | 適用項目 | 関連する見積項目 | 分電盤, 配線 |

        ### 使い方

        #### 1. 法令PDFのアップロード

        「法令アップロード」タブで、関係法令一覧PDFをアップロードします。

        **対応PDFの例**:
        - 関係法令一覧_追加１.pdf
        - 電気設備技術基準.pdf
        - ガス事業法抜粋.pdf

        #### 2. 抽出結果の確認

        AIが自動的に以下を抽出します：
        - 法令名・法令コード
        - カテゴリ（電気/ガス/共通等）
        - 重要度スコア
        - 適用される見積項目

        #### 3. データベースへの保存

        「法令KBに保存」ボタンで保存します。

        ### 見積作成での活用

        見積書作成時、登録された法令情報を参照して：

        1. **法令遵守チェック**: 必要な法令対応項目の確認
        2. **根拠情報**: 見積項目の法的根拠を記録
        3. **備考欄**: 適用法令を自動記載

        ### 重要度スコアの基準

        | スコア | 意味 | 例 |
        |--------|------|-----|
        | 1.0 | 必須遵守 | 電気設備技術基準 |
        | 0.8 | 高重要度 | 内線規程(JEAC8001) |
        | 0.5 | 参照推奨 | 学校施設設備基準 |
        | 0.3 | 参考程度 | 各種ガイドライン |
        """)


if __name__ == "__main__":
    main()
