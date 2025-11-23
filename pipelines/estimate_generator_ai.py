"""
AI自動見積生成エンジン

仕様書から直接、建築設備の専門知識を使って詳細な見積項目を自動生成します。
参照見積書不要で、AIが設計レベルの詳細項目を推定します。
"""

import os
import json
import re
import io
import base64
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv
from anthropic import Anthropic
from loguru import logger
import PyPDF2

# ログ設定（ファイル出力含む）
try:
    from pipelines.logging_config import setup_logging
    setup_logging()
except ImportError:
    pass  # スタンドアロン実行時は既存のlogger設定を使用

try:
    import fitz  # PyMuPDF for image extraction
    from PIL import Image
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    logger.warning("PyMuPDF not available - drawing extraction disabled")

# ベクトル検索用ライブラリ
try:
    import numpy as np
    import faiss
    from sentence_transformers import SentenceTransformer
    HAS_VECTOR_SEARCH = True
except ImportError:
    HAS_VECTOR_SEARCH = False
    logger.warning("FAISS/sentence-transformers not available - vector search disabled")

from pipelines.schemas import (
    EstimateItem, DisciplineType, FMTDocument, ProjectInfo, FacilityType,
    CostType
)
from pipelines.cost_tracker import record_cost
from pipelines.estimation_rules import EstimationChecker, get_checklist_summary


def repair_json_array(json_str: str) -> str:
    """
    LLMが返す不正なJSON配列を修復

    よくある問題:
    - [ "name": ... ] → [ { "name": ... } ]
    - オブジェクト間の } , { が欠落
    """
    import re

    # 空白を正規化
    json_str = json_str.strip()

    # 配列の開始直後に { がない場合、追加
    # [ の後に空白と "key": が来るパターンを検出
    if re.match(r'^\[\s*"[a-zA-Z_]+":', json_str):
        logger.warning("Detected malformed JSON array - missing opening braces")

        # 各オブジェクトの区切りを見つけて修正
        # "item_no": または "name": で始まる部分をオブジェクトの開始とみなす
        lines = json_str.split('\n')
        fixed_lines = []
        in_object = False
        brace_count = 0

        for i, line in enumerate(lines):
            stripped = line.strip()

            # 配列の開始
            if stripped == '[':
                fixed_lines.append(line)
                continue

            # 配列の終了
            if stripped == ']':
                if in_object:
                    fixed_lines.append('  }')
                    in_object = False
                fixed_lines.append(line)
                continue

            # オブジェクトの開始を検出（"item_no": または "name": で始まる）
            if re.match(r'\s*"(item_no|name)":', stripped) and not in_object:
                fixed_lines.append('  {')
                in_object = True

            # オブジェクトの終了を検出（confidence の後、または空行の前）
            if in_object and re.match(r'\s*"(confidence|estimation_basis)":', stripped):
                fixed_lines.append(line)
                # 次の行が新しいオブジェクトの開始かチェック
                if i + 1 < len(lines):
                    next_stripped = lines[i + 1].strip()
                    if next_stripped.startswith('"item_no"') or next_stripped.startswith('"name"'):
                        fixed_lines.append('  },')
                        in_object = False
                continue

            fixed_lines.append(line)

        json_str = '\n'.join(fixed_lines)

    return json_str


def extract_json_array_robust(text: str) -> List[Dict]:
    """
    テキストからJSON配列を堅牢に抽出する

    様々なLLMの出力形式に対応：
    - マークダウンコードブロック（```json ... ```）
    - 説明文の後のJSON
    - ネストされたコードブロック
    - 不完全なJSON（末尾切れ等）

    Args:
        text: LLMからの応答テキスト

    Returns:
        パースされたJSONオブジェクトのリスト、パース失敗時は空リスト
    """
    import re

    if not text:
        return []

    # 1. マークダウンコードブロックを除去
    text_clean = re.sub(r'```json\s*\n?', '', text)
    text_clean = re.sub(r'```\s*\n?', '', text_clean)

    # 2. JSON配列を見つける（最初の [ から最後の ] まで）
    json_start = text_clean.find('[')
    json_end = text_clean.rfind(']')

    if json_start < 0:
        logger.warning("No JSON array found in response")
        return []

    if json_end < 0 or json_end <= json_start:
        # ] が見つからない場合、途中までをパースしてみる
        logger.warning("No closing bracket found, attempting partial parse")
        json_str = text_clean[json_start:]
        # 最後の完全なオブジェクトを見つける
        last_brace = json_str.rfind('}')
        if last_brace > 0:
            json_str = json_str[:last_brace+1] + ']'
    else:
        json_str = text_clean[json_start:json_end+1]

    # 3. JSON修復を試みる
    json_str = repair_json_array(json_str)

    # 4. パースを試みる
    try:
        items = json.loads(json_str)
        if isinstance(items, list):
            logger.debug(f"Successfully parsed {len(items)} items")
            return items
        else:
            logger.warning(f"Parsed JSON is not a list: {type(items)}")
            return []
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error: {e}")

        # 5. 最後の完全なオブジェクトまでで再試行
        last_complete = json_str.rfind('},')
        if last_complete > 0:
            json_str_truncated = json_str[:last_complete+1] + ']'
            try:
                items = json.loads(json_str_truncated)
                logger.info(f"Recovered {len(items)} items after truncation")
                return items
            except json.JSONDecodeError as e2:
                logger.error(f"Recovery failed: {e2}")

        # 6. 個別オブジェクトを抽出（最終手段）
        try:
            # {...} パターンを全て抽出
            obj_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            matches = re.findall(obj_pattern, json_str)
            items = []
            for match in matches:
                try:
                    obj = json.loads(match)
                    if isinstance(obj, dict) and 'name' in obj:
                        items.append(obj)
                except json.JSONDecodeError:
                    continue
            if items:
                logger.info(f"Extracted {len(items)} items using pattern matching")
                return items
        except Exception as e3:
            logger.error(f"Pattern extraction failed: {e3}")

        return []


# ===== Phase 2: 類義語辞書（KBマッチング精度向上用）=====
SYNONYM_DICT = {
    # 電気設備 - 受変電
    "気中開閉器": ["PAS", "高圧気中負荷開閉器", "高圧気中開閉器", "気中負荷開閉器", "LBS", "負荷開閉器"],
    "架橋ポリエチレンケーブル": ["CV", "CVT", "高圧ケーブル", "CVケーブル", "CVTケーブル", "CVQ", "CVD"],
    "キュービクル": ["高圧受電設備", "受変電設備", "受電設備", "変電設備", "高圧盤"],
    "変圧器": ["トランス", "Tr", "単相変圧器", "三相変圧器", "乾式変圧器", "油入変圧器"],
    "遮断器": ["VCB", "真空遮断器", "気中遮断器", "ACB", "MCCB", "配線用遮断器", "ブレーカー"],
    "進相コンデンサ": ["SC", "力率改善コンデンサ", "コンデンサ"],
    # 電気設備 - 配線
    "ビニル絶縁電線": ["IV", "IV電線", "600V IV", "HIV", "耐熱電線"],
    "VVFケーブル": ["VVF", "Fケーブル", "平形ケーブル"],
    "CVVケーブル": ["CVV", "制御ケーブル"],
    "電線管": ["金属管", "薄鋼電線管", "厚鋼電線管", "E管", "C管", "PF管", "CD管", "合成樹脂管"],
    "ケーブルラック": ["ラック", "ケーブルトレイ", "配線ダクト"],
    "プルボックス": ["PB", "ジョイントボックス", "JB"],
    # 電気設備 - 盤類
    "接地工事": ["A種接地", "B種接地", "C種接地", "D種接地", "接地", "アース", "接地極"],
    "分電盤": ["動力盤", "電灯盤", "配電盤", "動力制御盤", "OA盤"],
    "制御盤": ["操作盤", "監視盤", "中央監視盤"],
    "端子盤": ["TB", "端子台"],
    # 電気設備 - 照明
    "LED照明": ["LED", "LED器具", "照明器具", "LED灯", "LEDベースライト", "LED一体型"],
    "非常照明": ["非常用照明", "誘導灯", "避難誘導灯", "非常灯"],
    "ダウンライト": ["DL", "埋込照明", "天井埋込"],
    "スポットライト": ["SL", "スポット"],
    "高天井照明": ["投光器", "水銀灯", "メタルハライド", "HID"],
    # 電気設備 - 弱電
    "自動火災報知設備": ["自火報", "火災報知器", "火報", "感知器", "煙感知器", "熱感知器"],
    "インターホン": ["インターフォン", "ドアホン", "呼出設備"],
    "LAN配線": ["情報コンセント", "LANコンセント", "データコンセント", "カテゴリ6"],
    "テレビ共聴設備": ["CATV", "TV共聴", "アンテナ"],
    # 機械設備 - 空調
    "空冷ヒートポンプ": ["エアコン", "空調機", "ヒートポンプ", "EHP", "パッケージエアコン", "PAC"],
    "ガスヒートポンプ": ["GHP", "ガスエアコン", "ガスヒーポン"],
    "ビル用マルチ": ["マルチエアコン", "VRF", "ビルマル"],
    "全熱交換器": ["ロスナイ", "熱交換換気", "熱交換ユニット"],
    "換気扇": ["換気設備", "排気ファン", "給気ファン", "天井扇", "有圧換気扇"],
    "ダクト": ["亜鉛鉄板ダクト", "スパイラルダクト", "フレキシブルダクト", "フレキ"],
    "制気口": ["吹出口", "吸込口", "アネモ", "ライン型", "VHS"],
    # 機械設備 - 給排水
    "給水ポンプ": ["加圧給水ポンプ", "揚水ポンプ", "ポンプユニット", "給水ユニット"],
    "排水ポンプ": ["汚水ポンプ", "雑排水ポンプ", "水中ポンプ"],
    "受水槽": ["貯水槽", "FRP受水槽", "地下水槽", "高架水槽"],
    "給湯器": ["電気温水器", "ガス給湯器", "給湯設備", "貯湯槽", "エコキュート"],
    "衛生器具": ["便器", "洗面器", "流し台", "手洗器", "小便器", "大便器", "洗面化粧台"],
    "給水管": ["水道管", "VP管", "HIVP", "ライニング鋼管", "ステンレス管", "SUS管"],
    "排水管": ["VU管", "排水用硬質塩ビ管", "鋳鉄管", "DVLP"],
    # 機械設備 - 消火
    "消火栓": ["屋内消火栓", "屋外消火栓", "連結送水管"],
    "スプリンクラー": ["SP", "スプリンクラーヘッド", "SPヘッド", "閉鎖型SP"],
    "消火器": ["ABC消火器", "粉末消火器"],
    # ガス設備
    "白ガス管": ["鋼管", "ガス管", "SGP", "配管用炭素鋼鋼管", "白管"],
    "カラー鋼管": ["塗覆装鋼管", "被覆鋼管", "カラー管"],
    "PE管": ["ポリエチレン管", "ポリ管", "樹脂管", "PE80", "PE100"],
    "ガスコンセント": ["ガス栓", "コンセント", "ガス接続口"],
    "ネジコック": ["コック", "バルブ", "ガスコック", "ガスバルブ", "止めコック"],
    "分岐コック": ["分岐バルブ", "チーズ", "分岐管"],
    "ボールバルブ": ["ボール弁", "ボールスライドジョイント", "BSJ"],
    "ガスメーター": ["メーター", "マイコンメーター", "計量器"],
    "ガス漏れ警報器": ["ガス警報器", "ガス検知器", "警報器"],
    # 電気設備 - 追加
    "照明配線": ["電灯配線", "照明回路", "電灯回路", "照明工事"],
    "コンセント配線": ["コンセント回路", "コンセント工事"],
    "接地": ["接地工事", "アース工事", "アース", "接地極", "A種接地", "B種接地", "C種接地", "D種接地"],
    "避雷針": ["避雷設備", "雷保護", "避雷導体"],
    "避雷導線": ["避雷導体", "接地導線"],
    # 機械設備 - 追加
    "全熱交換機": ["全熱交換器", "ロスナイ", "熱交換換気", "熱交換ユニット", "HRV", "ERV", "換気ユニット"],
    "配管支持金物": ["配管支持金具", "支持金物", "支持金具", "サポート", "配管サポート", "バンド"],
    "ドレン配管": ["ドレン管", "排水管", "結露水配管", "ドレンホース"],
    "冷媒配管": ["冷媒管", "ペアコイル", "被覆銅管", "冷媒チューブ"],
    "給水栓": ["蛇口", "水栓", "カラン", "給水口"],
    "エレベーター": ["昇降機", "EV", "リフト"],
    # ガス設備 - 追加
    "緊急遮断弁": ["遮断弁", "緊急弁", "ガス遮断弁", "安全弁"],
    "ヒューズコック": ["ヒューズガス栓", "過流出防止弁", "ヒューズ付コック"],
    "配管保温": ["保温工事", "保温材", "グラスウール", "保温被覆"],
    "舗装復旧": ["アスファルト復旧", "道路復旧", "舗装工事", "復旧工事"],
    # 共通 - 仮設・諸経費
    "諸経費": ["一般管理費", "現場管理費", "共通仮設費"],
    "足場": ["枠組足場", "単管足場", "移動式足場", "ローリングタワー"],
    "養生": ["床養生", "壁養生", "防護", "シート養生"],
    "産業廃棄物処分": ["産廃処分", "廃棄物処理", "残材処分", "ガラ処分"],
    "運搬費": ["搬入費", "搬出費", "資機材運搬", "小運搬"],
}

# 高額機器リスト（単価妥当性チェック用）
# 高額機器の最低価格（本体・一式の場合のみ適用）
# 注意：「点検」「保守」「配管」「配線」等を含む項目には適用しない
HIGH_VALUE_ITEMS = {
    "キュービクル": 800000,       # 最低80万円
    "高圧受電設備": 800000,
    "受変電設備": 800000,
    "変圧器": 300000,             # 最低30万円
    "高圧変圧器": 300000,
    "発電機": 1500000,            # 最低150万円
    "非常用発電機": 1500000,
    "エレベーター": 3000000,      # 最低300万円（本体のみ）
    "昇降機設備": 3000000,        # 本体を含む設備工事
}

# 高額チェックを除外するキーワード（部品、作業、保守等）
HIGH_VALUE_EXCLUDE_KEYWORDS = [
    "点検", "保守", "配管", "配線", "試験", "調整", "工事", "設置",
    "室内機", "室外機", "リモコン", "制御盤", "操作盤", "保護",
    "清掃", "撤去", "更新", "改修"
]

# 一般項目の最大価格（誤マッチング防止用）
# これらの項目に高額な単価がマッチした場合は拒否する
MAX_PRICE_ITEMS = {
    "フェンス": 100000,           # 最大10万円/m
    "電話機": 50000,              # 最大5万円/台
    "電話": 100000,               # 最大10万円
    "インターホン": 80000,        # 最大8万円/台
    "コンセント": 20000,          # 最大2万円/箇所
    "スイッチ": 10000,            # 最大1万円/個
    "照明器具": 100000,           # 最大10万円/台
    "感知器": 30000,              # 最大3万円/個
    "配線": 50000,                # 最大5万円/m
    "ケーブル": 30000,            # 最大3万円/m
    "接続": 500000,               # 最大50万円/式
}


# ===== ベクトル検索クラス =====
class VectorKBSearch:
    """
    FAISSを使用したKBベクトル検索

    sentence-transformersで日本語テキストをベクトル化し、
    FAISSで高速な類似度検索を行います。
    """

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small"):
        """
        Args:
            model_name: 使用するembeddingモデル名
                推奨: "intfloat/multilingual-e5-small" (高速・日本語対応)
                高精度: "intfloat/multilingual-e5-base"
        """
        if not HAS_VECTOR_SEARCH:
            logger.warning("Vector search not available - using fallback string matching")
            self.model = None
            self.index = None
            self.kb_items = []
            return

        logger.info(f"Initializing vector search with model: {model_name}")
        try:
            self.model = SentenceTransformer(model_name)
            self.index = None
            self.kb_items = []
            self.dimension = self.model.get_sentence_embedding_dimension()
            logger.info(f"Vector search initialized (dimension={self.dimension})")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            self.model = None
            self.index = None
            self.kb_items = []

    def build_index(self, kb_items: List[Dict]) -> bool:
        """
        KBアイテムからFAISSインデックスを構築

        Args:
            kb_items: KB項目のリスト

        Returns:
            成功した場合True
        """
        if not self.model or not kb_items:
            return False

        self.kb_items = kb_items

        # KB項目からテキストを生成（項目名 + 仕様 + 工事区分）
        texts = []
        for item in kb_items:
            desc = item.get("description", "")
            spec = item.get("features", {}).get("specification", "")
            discipline = item.get("discipline", "")
            # E5モデル用のプレフィックス
            text = f"passage: {desc} {spec} {discipline}"
            texts.append(text)

        logger.info(f"Building vector index for {len(texts)} KB items...")

        try:
            # ベクトル化
            embeddings = self.model.encode(texts, show_progress_bar=False)
            embeddings = np.array(embeddings).astype('float32')

            # FAISSインデックス構築（L2距離）
            self.index = faiss.IndexFlatIP(self.dimension)  # 内積（コサイン類似度用）

            # 正規化（コサイン類似度計算のため）
            faiss.normalize_L2(embeddings)
            self.index.add(embeddings)

            logger.info(f"Vector index built successfully: {self.index.ntotal} vectors")
            return True

        except Exception as e:
            logger.error(f"Failed to build vector index: {e}")
            return False

    def search(self, query: str, discipline: str = None, top_k: int = 5) -> List[Dict]:
        """
        クエリに類似したKB項目を検索

        Args:
            query: 検索クエリ（項目名 + 仕様）
            discipline: 工事区分でフィルタ（任意）
            top_k: 返す結果数

        Returns:
            類似KB項目のリスト（スコア付き）
        """
        if not self.model or not self.index or self.index.ntotal == 0:
            return []

        try:
            # クエリをベクトル化（E5モデル用プレフィックス）
            query_text = f"query: {query}"
            query_embedding = self.model.encode([query_text], show_progress_bar=False)
            query_embedding = np.array(query_embedding).astype('float32')
            faiss.normalize_L2(query_embedding)

            # 検索（多めに取得してフィルタ後に絞る）
            search_k = top_k * 3 if discipline else top_k
            distances, indices = self.index.search(query_embedding, min(search_k, len(self.kb_items)))

            results = []
            for i, (dist, idx) in enumerate(zip(distances[0], indices[0])):
                if idx < 0 or idx >= len(self.kb_items):
                    continue

                kb_item = self.kb_items[idx]

                # 工事区分フィルタ
                if discipline:
                    kb_discipline = kb_item.get("discipline", "")
                    if discipline not in kb_discipline and kb_discipline not in discipline:
                        if kb_discipline != "設備工事":  # 汎用項目は許可
                            continue

                results.append({
                    "kb_item": kb_item,
                    "score": float(dist),  # コサイン類似度（0-1）
                    "rank": len(results) + 1
                })

                if len(results) >= top_k:
                    break

            return results

        except Exception as e:
            logger.error(f"Vector search error: {e}")
            return []

    def is_available(self) -> bool:
        """ベクトル検索が利用可能かどうか"""
        return self.model is not None and self.index is not None and self.index.ntotal > 0


class AIEstimateGenerator:
    """
    AI自動見積生成器

    仕様書から建物情報を抽出し、建築設備の専門知識を使って
    詳細な見積項目（配管サイズ、数量、材料等）を自動生成します。
    """

    def __init__(self, kb_path: str = "kb/price_kb.json", use_vector_search: bool = True):
        load_dotenv()
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self.kb_path = kb_path
        self.price_kb = self._load_price_kb()

        # ベクトル検索の初期化
        self.vector_search = None
        self.use_vector_search = use_vector_search
        if use_vector_search and HAS_VECTOR_SEARCH and self.price_kb:
            self._init_vector_search()

    def _load_price_kb(self) -> List[Dict]:
        """価格KBを読み込み"""
        if os.path.exists(self.kb_path):
            with open(self.kb_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        logger.warning(f"Price KB not found: {self.kb_path}")
        return []

    def _init_vector_search(self):
        """ベクトル検索インデックスを初期化"""
        logger.info("Initializing vector search for KB...")
        self.vector_search = VectorKBSearch()
        if self.vector_search.model:
            success = self.vector_search.build_index(self.price_kb)
            if success:
                logger.info(f"Vector search ready: {len(self.price_kb)} KB items indexed")
            else:
                logger.warning("Vector search index build failed - using fallback")
                self.vector_search = None
        else:
            logger.warning("Vector search model not loaded - using fallback")
            self.vector_search = None

    def _vector_search_match(self, item_name: str, item_spec: str, discipline: str) -> Optional[Dict]:
        """
        ベクトル検索でKBマッチングを行う

        Args:
            item_name: 見積項目名
            item_spec: 仕様
            discipline: 工事区分

        Returns:
            最良マッチのKB項目とスコア、またはNone
        """
        if not self.vector_search or not self.vector_search.is_available():
            return None

        # クエリ生成
        query = f"{item_name} {item_spec}".strip()
        if not query:
            return None

        # ベクトル検索実行
        results = self.vector_search.search(query, discipline=discipline, top_k=3)

        if results and results[0]["score"] >= 0.3:  # 類似度閾値緩和: 0.5 → 0.3
            best = results[0]
            logger.debug(f"Vector match: '{query}' → '{best['kb_item'].get('description')}' "
                        f"(score={best['score']:.3f})")
            return best

        return None

    # ===== Phase 2: KBマッチング改善用ヘルパーメソッド =====

    def _is_discipline_compatible(self, kb_discipline: str, item_discipline: str) -> bool:
        """
        工事区分の互換性をチェック（緩和版）

        Args:
            kb_discipline: KBの工事区分（例: "設備工事", "電気設備工事"）
            item_discipline: 見積項目の工事区分（例: "電気設備工事"）

        Returns:
            互換性があればTrue
        """
        if not kb_discipline or not item_discipline:
            return True  # 空の場合は互換性ありとみなす

        # 完全一致
        if kb_discipline == item_discipline:
            return True

        # "設備工事" は全ての設備工事にマッチ
        if kb_discipline == "設備工事":
            return True

        # 部分一致（"電気" in "電気設備工事" など）
        if kb_discipline in item_discipline or item_discipline in kb_discipline:
            return True

        # 設備工事の短縮形対応
        discipline_aliases = {
            "電気": "電気設備工事",
            "機械": "機械設備工事",
            "ガス": "ガス設備工事",
        }
        for alias, full_name in discipline_aliases.items():
            if (kb_discipline == alias and item_discipline == full_name) or \
               (kb_discipline == full_name and item_discipline == alias):
                return True

        return False

    def _find_synonyms(self, item_name: str) -> List[str]:
        """
        項目名の類義語を取得

        Args:
            item_name: 見積項目名

        Returns:
            類義語リスト（元の項目名を含む）
        """
        synonyms = [item_name]

        # 正規化した項目名
        item_name_norm = self._normalize_text(item_name)

        for key, values in SYNONYM_DICT.items():
            key_norm = self._normalize_text(key)

            # キーが項目名に含まれる、または項目名がキーに含まれる
            if key_norm in item_name_norm or item_name_norm in key_norm:
                synonyms.extend(values)
                synonyms.append(key)
                continue

            # 類義語が項目名に含まれる
            for value in values:
                value_norm = self._normalize_text(value)
                if value_norm in item_name_norm or item_name_norm in value_norm:
                    synonyms.append(key)
                    synonyms.extend(values)
                    break

        return list(set(synonyms))

    def _validate_price(self, item_name: str, matched_price: float) -> bool:
        """
        マッチした単価が妥当かチェック

        Args:
            item_name: 見積項目名
            matched_price: マッチした単価

        Returns:
            妥当であればTrue
        """
        if matched_price is None:
            return True

        item_name_norm = self._normalize_text(item_name)

        # 除外キーワードをチェック（部品、作業、保守等は除外）
        for exclude_kw in HIGH_VALUE_EXCLUDE_KEYWORDS:
            if exclude_kw in item_name:
                return True  # 除外対象なので検証をスキップ

        # 最低価格チェック（高額機器）
        for keyword, min_price in HIGH_VALUE_ITEMS.items():
            keyword_norm = self._normalize_text(keyword)
            if keyword_norm in item_name_norm:
                if matched_price < min_price:
                    logger.warning(
                        f"Price validation failed: '{item_name}' matched ¥{matched_price:,.0f} "
                        f"but minimum expected is ¥{min_price:,.0f}"
                    )
                    return False

        # 最大価格チェック（一般項目）- 誤マッチング防止
        for keyword, max_price in MAX_PRICE_ITEMS.items():
            keyword_norm = self._normalize_text(keyword)
            if keyword_norm in item_name_norm:
                if matched_price > max_price:
                    logger.warning(
                        f"Price validation failed: '{item_name}' matched ¥{matched_price:,.0f} "
                        f"but maximum expected is ¥{max_price:,.0f}"
                    )
                    return False

        return True

    def _check_unit_compatibility(self, item_unit: str, kb_unit: str) -> bool:
        """
        単位の互換性をチェック（強化版）

        Args:
            item_unit: 見積項目の単位
            kb_unit: KB項目の単位

        Returns:
            互換性があればTrue
        """
        if not item_unit or not kb_unit:
            return True

        # 正規化
        unit_norm_item = self._normalize_text(item_unit)
        unit_norm_kb = self._normalize_text(kb_unit)

        # 完全一致または包含
        if unit_norm_item == unit_norm_kb:
            return True
        if unit_norm_item in unit_norm_kb or unit_norm_kb in unit_norm_item:
            return True

        # 互換性のない単位ペア（強化版）
        # 「式」「基」「面」「台」「組」は他の計量単位と互換性なし
        lump_sum_units = ["式", "基", "面", "台", "組", "セット", "set", "ユニット"]
        quantity_units = ["m", "ｍ", "本", "個", "箇所", "ケ所", "ヶ所", "点", "口"]

        # どちらかが一式系で、もう一方が計量系なら互換性なし
        item_is_lump = any(u in unit_norm_item for u in lump_sum_units)
        kb_is_lump = any(u in unit_norm_kb for u in lump_sum_units)
        item_is_qty = any(u in unit_norm_item for u in quantity_units)
        kb_is_qty = any(u in unit_norm_kb for u in quantity_units)

        if (item_is_lump and kb_is_qty) or (item_is_qty and kb_is_lump):
            logger.debug(f"Unit incompatible: '{item_unit}' vs '{kb_unit}'")
            return False

        # 追加の互換性なしペア
        incompatible_pairs = [
            ("箇所", "m"), ("個", "m"), ("台", "m"), ("ヶ所", "m"), ("ケ所", "m"),
            ("点", "m"), ("口", "m"), ("面", "m"), ("基", "m"),
        ]
        for u1, u2 in incompatible_pairs:
            if (u1 in unit_norm_item and u2 in unit_norm_kb) or \
               (u2 in unit_norm_item and u1 in unit_norm_kb):
                return False

        return True

    def _check_price_sanity(self, item_name: str, item_unit: str, unit_price: float, quantity: float) -> bool:
        """
        単価と金額の妥当性をチェック（異常な高額マッチングを防ぐ）

        Args:
            item_name: 項目名
            item_unit: 単位
            unit_price: 単価
            quantity: 数量

        Returns:
            妥当であればTrue
        """
        if not unit_price or not quantity:
            return True

        # 計算される金額
        amount = unit_price * quantity

        # 単位ごとの単価上限（異常検出用）
        unit_price_limits = {
            "m": 50000,      # 配管/配線は5万円/m以下が妥当
            "ｍ": 50000,
            "本": 100000,    # 10万円/本以下
            "個": 50000,     # 5万円/個以下
            "箇所": 100000,  # 10万円/箇所以下
            "ケ所": 100000,
            "ヶ所": 100000,
            "点": 50000,     # 5万円/点以下
            "口": 50000,     # 5万円/口以下
        }

        # 単価上限チェック
        for unit_key, max_price in unit_price_limits.items():
            if unit_key in (item_unit or ""):
                if unit_price > max_price:
                    logger.warning(
                        f"Price sanity check failed: '{item_name}' "
                        f"¥{unit_price:,.0f}/{item_unit} > max ¥{max_price:,}/{unit_key}"
                    )
                    return False

        # 金額上限チェック（単一項目で1億円超は異常）
        if amount > 100000000:
            logger.warning(
                f"Amount sanity check failed: '{item_name}' "
                f"¥{amount:,.0f} > max ¥100,000,000"
            )
            return False

        return True

    def _call_api_with_cost_tracking(
        self,
        prompt: str,
        operation: str,
        max_tokens: int = 8000,
        metadata: Optional[Dict] = None
    ):
        """API呼び出しとコスト追跡を行う共通メソッド"""
        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )

        # コスト記録
        record_cost(
            operation=operation,
            model_name=self.model_name,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            metadata=metadata or {}
        )

        return response

    def extract_text_from_pdf(self, pdf_path: str, max_pages: int = None) -> str:
        """PDFからテキストを抽出（ページ番号マーカー付き）"""
        logger.info(f"Extracting text from PDF: {pdf_path}")

        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                total_pages = len(pdf_reader.pages) if max_pages is None else min(len(pdf_reader.pages), max_pages)
                for page_num in range(total_pages):
                    page_text = pdf_reader.pages[page_num].extract_text()
                    # ページ番号マーカーを追加（セクション特定用）
                    text += f"\n[PAGE {page_num + 1}/{total_pages}]\n"
                    text += page_text + "\n"

            logger.info(f"Extracted {len(text)} characters from {total_pages} pages")
            return text
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            return ""

    def extract_text_from_pages(self, pdf_path: str, start_page: int, end_page: int) -> str:
        """特定ページ範囲のテキストを抽出"""
        logger.info(f"Extracting text from pages {start_page}-{end_page}: {pdf_path}")

        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page_num in range(start_page - 1, min(end_page, len(pdf_reader.pages))):
                    page_text = pdf_reader.pages[page_num].extract_text()
                    text += f"\n[PAGE {page_num + 1}]\n"
                    text += page_text + "\n"

            logger.info(f"Extracted {len(text)} characters from pages {start_page}-{end_page}")
            return text
        except Exception as e:
            logger.error(f"Error extracting text from pages: {e}")
            return ""

    def detect_specification_table_pages(self, spec_text: str) -> List[int]:
        """諸元表が含まれるページを検出"""
        table_keywords = ["諸元表", "室名", "床面積", "天井高", "空調", "給排水", "ガス栓"]
        pages = []

        # ページマーカーで分割
        import re
        page_pattern = r'\[PAGE (\d+)(?:/\d+)?\]'
        page_splits = re.split(page_pattern, spec_text)

        for i in range(1, len(page_splits), 2):
            page_num = int(page_splits[i])
            page_content = page_splits[i + 1] if i + 1 < len(page_splits) else ""

            # キーワードマッチでページを検出
            keyword_count = sum(1 for kw in table_keywords if kw in page_content)
            if keyword_count >= 3:  # 3つ以上のキーワードがあれば諸元表ページ
                pages.append(page_num)
                logger.info(f"Detected specification table on page {page_num} (keywords: {keyword_count})")

        return pages

    def extract_specification_tables(self, pdf_path: str, spec_text: str) -> Dict[str, Any]:
        """
        諸元表から部屋・設備情報を抽出

        Returns:
            {
                "rooms": [{"name": "普通教室", "area": 63.0, "gas_outlets": 0, ...}, ...],
                "equipment_summary": {"total_gas_outlets": 38, "total_area": 2145, ...}
            }
        """
        logger.info("Extracting specification tables")

        # 諸元表ページを検出
        table_pages = self.detect_specification_table_pages(spec_text)

        if not table_pages:
            # キーワード検出できない場合、典型的なページ範囲を試行（後半ページ）
            logger.info("No table pages detected by keywords, trying pages 35-45")
            table_pages = list(range(35, 46))

        # 該当ページのテキストを抽出
        table_text = self.extract_text_from_pages(pdf_path, min(table_pages), max(table_pages))

        if not table_text or len(table_text) < 100:
            logger.warning("Could not extract specification table text")
            return {"rooms": [], "equipment_summary": {}}

        # Claude APIで構造化データに変換
        prompt = f"""以下は建物仕様書の諸元表（部屋一覧表）のテキストです。
各部屋の情報を構造化データとして抽出してください。

諸元表テキスト:
{table_text[:15000]}

【抽出する情報】
各部屋について以下を抽出し、JSON形式で出力してください：

```json
{{
  "rooms": [
    {{
      "room_name": "部屋名",
      "floor": "階",
      "area_m2": 63.0,
      "ceiling_height_m": 2.7,
      "has_air_conditioning": true,
      "has_ventilation": true,
      "has_water_supply": false,
      "has_drainage": false,
      "gas_outlets": 0,
      "electrical_outlets": 4,
      "lighting_count": 6,
      "remarks": "備考"
    }}
  ],
  "equipment_summary": {{
    "total_rooms": 20,
    "total_area_m2": 2145,
    "total_gas_outlets": 38,
    "rooms_with_gas": 5,
    "rooms_with_water": 8
  }}
}}
```

必ずJSON形式で回答してください。データがない項目はnullとしてください。"""

        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=8000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )

        # コスト記録
        record_cost(
            operation="諸元表テキスト抽出",
            model_name=self.model_name,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            metadata={"source": "extract_specification_table"}
        )

        response_text = response.content[0].text

        # JSONを抽出
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1

        if json_start == -1:
            logger.error("No JSON object found in specification table response")
            return {"rooms": [], "equipment_summary": {}}

        # 閉じ括弧がない場合は、truncated JSONとして処理
        if json_end <= json_start:
            logger.warning("Specification table response appears truncated")
            return {"rooms": [], "equipment_summary": {}}

        json_str = response_text[json_start:json_end]

        try:
            table_data = json.loads(json_str)
            rooms_count = len(table_data.get("rooms", []))
            logger.info(f"Extracted {rooms_count} rooms from specification tables")
            return table_data
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in specification tables: {e}")
            return {"rooms": [], "equipment_summary": {}}

    # ===== Phase 1: Vision抽出による諸元表データ取得 =====

    def extract_specification_table_with_vision(
        self, pdf_path: str, target_pages: List[int] = None
    ) -> Dict[str, Any]:
        """
        諸元表ページを画像として抽出し、Claude Vision APIで構造化データに変換

        Args:
            pdf_path: PDFファイルパス
            target_pages: 諸元表のページ番号リスト（1-indexed）。Noneの場合は39-40を使用

        Returns:
            {
                "rooms": [
                    {"name": "普通教室", "count": 21, "floor": "各階",
                     "gas_outlets": 0, "electrical_outlets": 4, ...},
                ],
                "totals": {
                    "room_count": 50,
                    "gas_outlet_total": 38,
                    "electrical_outlet_total": 200
                }
            }
        """
        if not HAS_PYMUPDF:
            logger.warning("PyMuPDF not available - Vision extraction disabled")
            return {"rooms": [], "totals": {}}

        if target_pages is None:
            target_pages = [39, 40]  # デフォルトは諸元表のページ

        logger.info(f"Extracting specification tables with Vision from pages {target_pages}")

        try:
            doc = fitz.open(pdf_path)
            all_rooms = []
            totals = {
                "room_count": 0,
                "gas_outlet_total": 0,
                "electrical_outlet_total": 0,
                "total_area_m2": 0
            }

            for page_num in target_pages:
                if page_num > len(doc):
                    continue

                page = doc[page_num - 1]  # 0-indexed

                # ページを高解像度画像に変換
                mat = fitz.Matrix(200/72, 200/72)  # 200 DPI
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")

                # Base64エンコード
                image_base64 = base64.b64encode(img_data).decode('utf-8')

                # Claude Vision APIで表を解析
                prompt = """この画像は建物仕様書の諸元表（部屋一覧表）です。
表形式のデータを正確に読み取り、以下の情報をJSON形式で抽出してください。

【抽出する情報】
各行（部屋）について：
- room_name: 部屋名（普通教室、生物室、調理室等）
- count: 部屋数（数字）
- floor: 階（各階、2、3等）
- gas_outlets: ガス栓数（○がある場合は1、数字があればその数、なければ0）
- electrical_outlets: コンセント数（○マークの数または記載の数）
- has_air_conditioning: 空調有無（○があればtrue）
- has_water_supply: 給水有無（○があればtrue）
- has_drainage: 排水有無（○があればtrue）
- lighting_type: 照明タイプ（直付下面開放、等）
- lighting_lux: 照度（500、400等の数値）

【出力形式】
```json
{
  "rooms": [
    {
      "room_name": "普通教室",
      "count": 21,
      "floor": "各階",
      "gas_outlets": 0,
      "electrical_outlets": 4,
      "has_air_conditioning": true,
      "has_water_supply": false,
      "has_drainage": false,
      "lighting_type": "直付下面開放",
      "lighting_lux": 500
    }
  ],
  "page_totals": {
    "room_count": 21,
    "gas_outlet_total": 0
  }
}
```

表の全ての行を抽出してください。○マークは「あり」を意味します。"""

                try:
                    response = self.client.messages.create(
                        model=self.model_name,
                        max_tokens=8000,
                        messages=[{
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": image_base64
                                    }
                                },
                                {"type": "text", "text": prompt}
                            ]
                        }]
                    )

                    # コスト記録
                    record_cost(
                        operation="諸元表Vision抽出",
                        model_name=self.model_name,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        metadata={"source": "extract_specification_table_with_vision", "page": page_num}
                    )

                    content = response.content[0].text

                    # JSONを抽出（マークダウンコードブロックを除去）
                    content = re.sub(r'```json\s*\n?', '', content)
                    content = re.sub(r'\n?```\s*$', '', content)
                    content = re.sub(r'\n?```\s*\n?', '', content)

                    json_start = content.find('{')
                    json_end = content.rfind('}') + 1

                    if json_start != -1 and json_end > json_start:
                        page_data = json.loads(content[json_start:json_end])
                        rooms = page_data.get("rooms", [])
                        all_rooms.extend(rooms)

                        # 集計
                        page_totals = page_data.get("page_totals", {})
                        for room in rooms:
                            count = room.get("count", 1) or 1
                            totals["room_count"] += count
                            totals["gas_outlet_total"] += (room.get("gas_outlets", 0) or 0) * count
                            totals["electrical_outlet_total"] += (room.get("electrical_outlets", 0) or 0) * count

                        logger.info(f"Page {page_num}: Extracted {len(rooms)} room types")

                except json.JSONDecodeError as e:
                    logger.warning(f"JSON parse error on page {page_num}: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Failed to process page {page_num}: {e}")
                    continue

            doc.close()

            logger.info(f"Vision extraction complete: {len(all_rooms)} room types, "
                       f"{totals['room_count']} total rooms, "
                       f"{totals['gas_outlet_total']} gas outlets")

            return {
                "rooms": all_rooms,
                "totals": totals
            }

        except Exception as e:
            logger.error(f"Error in Vision extraction: {e}")
            return {"rooms": [], "totals": {}}

    def extract_drawing_info(self, pdf_path: str, start_page: int = 41, end_page: int = 49) -> Dict[str, Any]:
        """
        図面ページから設備情報を抽出（Claude Vision API使用）

        Args:
            pdf_path: PDFファイルパス
            start_page: 図面開始ページ（1-indexed）
            end_page: 図面終了ページ（1-indexed）

        Returns:
            {
                "pipe_routes": [...],
                "equipment_locations": [...],
                "estimated_pipe_lengths": {...}
            }
        """
        if not HAS_PYMUPDF:
            logger.warning("PyMuPDF not available - skipping drawing extraction")
            return {"pipe_routes": [], "equipment_locations": [], "estimated_pipe_lengths": {}}

        logger.info(f"Extracting drawing information from pages {start_page}-{end_page}")

        try:
            doc = fitz.open(pdf_path)
            drawing_info = {
                "pipe_routes": [],
                "equipment_locations": [],
                "estimated_pipe_lengths": {},
                "drawing_types": []
            }

            # 図面ページを処理（最大5ページに制限してAPI呼び出しを節約）
            pages_to_process = list(range(start_page - 1, min(end_page, len(doc))))[:5]

            for page_num in pages_to_process:
                page = doc[page_num]

                # ページを画像に変換
                mat = fitz.Matrix(150/72, 150/72)  # 150 DPI
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")

                # Base64エンコード
                image_base64 = base64.b64encode(img_data).decode('utf-8')

                # Claude Vision APIで図面を分析
                prompt = """この画像は建物の設備図面です。以下の情報を抽出してJSON形式で出力してください：

1. 図面の種類（配置図、平面図、設備図、配管図など）
2. 確認できる設備・機器（ガス機器、配管、メーター等）
3. 配管ルートの概要（あれば）
4. 推定される配管延長（あれば）

```json
{
  "drawing_type": "図面の種類",
  "visible_equipment": ["機器1", "機器2"],
  "pipe_info": {
    "routes": ["ルート1の説明", "ルート2の説明"],
    "estimated_length_m": 数値またはnull
  },
  "remarks": "備考"
}
```

図面から読み取れる情報のみを記載してください。"""

                try:
                    response = self.client.messages.create(
                        model=self.model_name,
                        max_tokens=2000,
                        messages=[{
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": image_base64
                                    }
                                },
                                {"type": "text", "text": prompt}
                            ]
                        }]
                    )

                    # コスト記録
                    record_cost(
                        operation="図面Vision分析",
                        model_name=self.model_name,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        metadata={"source": "extract_drawing_info_with_vision", "page": page_num}
                    )

                    content = response.content[0].text

                    # JSONを抽出
                    json_start = content.find('{')
                    json_end = content.rfind('}') + 1
                    if json_start != -1 and json_end > json_start:
                        page_data = json.loads(content[json_start:json_end])
                        drawing_info["drawing_types"].append(page_data.get("drawing_type", f"Page {page_num + 1}"))

                        if page_data.get("visible_equipment"):
                            drawing_info["equipment_locations"].extend(page_data["visible_equipment"])

                        pipe_info = page_data.get("pipe_info", {})
                        if pipe_info.get("routes"):
                            drawing_info["pipe_routes"].extend(pipe_info["routes"])
                        if pipe_info.get("estimated_length_m"):
                            drawing_info["estimated_pipe_lengths"][f"page_{page_num + 1}"] = pipe_info["estimated_length_m"]

                        logger.info(f"Extracted drawing info from page {page_num + 1}: {page_data.get('drawing_type', 'Unknown')}")

                except Exception as e:
                    logger.warning(f"Failed to process drawing page {page_num + 1}: {e}")
                    continue

            doc.close()

            # 重複を除去
            drawing_info["equipment_locations"] = list(set(drawing_info["equipment_locations"]))

            logger.info(f"Drawing extraction complete: {len(drawing_info['equipment_locations'])} equipment items, {len(drawing_info['pipe_routes'])} pipe routes")
            return drawing_info

        except Exception as e:
            logger.error(f"Error extracting drawing info: {e}")
            return {"pipe_routes": [], "equipment_locations": [], "estimated_pipe_lengths": {}}

    def extract_building_info(self, spec_text: str) -> Dict[str, Any]:
        """
        仕様書から建物情報を詳細抽出

        建築設備設計に必要な情報を抽出します：
        - 建物面積、階数、部屋数
        - 用途、設備仕様
        - 工事条件
        """
        logger.info("Extracting detailed building information")

        prompt = f"""あなたは建築設備の専門家です。以下の仕様書から、設備設計に必要な建物情報を詳細に抽出してください。

仕様書:
{spec_text[:60000]}

【抽出する情報】
以下の情報をJSON形式で抽出してください：

```json
{{
  "project_name": "工事名",
  "client_name": "顧客名",
  "location": "工事場所",
  "contract_period": "工期・リース期間",

  "building_info": {{
    "total_floor_area": 2145,  // 延床面積（㎡）
    "floors": 2,  // 階数
    "building_type": "仮設校舎",  // 建物種別
    "num_rooms": 20,  // 部屋数（推定）
    "num_floors_above": 2,  // 地上階数
    "num_floors_below": 0,  // 地下階数
    "structure": "鉄骨造",  // 構造
    "is_temporary": true  // 仮設かどうか
  }},

  "facility_requirements": {{
    "gas": {{
      "required": true,
      "type": "都市ガス",
      "usage": "給湯、厨房機器",
      "num_connection_points": 38  // ガス栓数（推定）
    }},
    "electrical": {{
      "required": true,
      "voltage": "低圧",
      "estimated_capacity_kva": 150  // 推定容量（kVA）
    }},
    "mechanical": {{
      "required": true,
      "hvac_type": "空調設備",
      "plumbing": true
    }}
  }},

  "construction_conditions": {{
    "existing_building": true,  // 既存建物の有無
    "requires_demolition": true,  // 解体工事の要否
    "site_access": "良好",  // 現場アクセス
    "work_restrictions": "授業時間外"  // 作業制限
  }}
}}
```

必ずJSON形式で回答してください。コメント（//）は含めず、純粋なJSON形式で出力してください。"""

        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=8000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )

        # コスト記録
        record_cost(
            operation="建物情報抽出",
            model_name=self.model_name,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            metadata={"source": "extract_building_info"}
        )

        response_text = response.content[0].text

        # JSONを抽出
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1

        if json_start == -1:
            logger.error("No JSON object found in building info response")
            return {}

        # 閉じ括弧がない場合は、truncated JSONとして処理
        if json_end <= json_start:
            logger.warning("Building info response appears truncated")
            return {}

        json_str = response_text[json_start:json_end]

        # コメントを削除（//から行末まで）
        json_str = re.sub(r'//.*', '', json_str)

        building_info = json.loads(json_str)

        logger.info(f"Extracted building info: {building_info.get('project_name', 'N/A')}")
        return building_info

    def generate_detailed_items_for_gas(
        self,
        building_info: Dict[str, Any]
    ) -> List[EstimateItem]:
        """
        ガス設備の詳細見積項目をAI生成

        建物情報から、配管サイズ・数量・材料を設計レベルで推定します。
        """
        logger.info("Generating detailed gas equipment items")

        # 建物情報を文字列化
        building_summary = json.dumps(building_info, ensure_ascii=False, indent=2)

        # 諸元表データがあれば追加情報として活用
        spec_table_info = ""
        if "spec_table" in building_info:
            spec_table = building_info["spec_table"]
            rooms = spec_table.get("rooms", [])
            summary = spec_table.get("equipment_summary", {})
            if rooms:
                spec_table_info = f"""
【諸元表からの実データ】
- 総部屋数: {summary.get('total_rooms', len(rooms))}室
- 総面積: {summary.get('total_area_m2', 'N/A')}㎡
- ガス栓総数: {summary.get('total_gas_outlets', 'N/A')}箇所
- ガス使用部屋数: {summary.get('rooms_with_gas', 'N/A')}室

部屋別詳細（抜粋）:
"""
                for room in rooms[:10]:  # 最大10室分を表示
                    gas_info = f"ガス栓{room.get('gas_outlets', 0)}個" if room.get('gas_outlets') else "ガスなし"
                    spec_table_info += f"- {room.get('room_name', '不明')}: {room.get('area_m2', '?')}㎡, {gas_info}\n"

        # 図面データがあれば追加情報として活用
        drawing_info_text = ""
        if "drawing_info" in building_info:
            drawing_data = building_info["drawing_info"]
            equipment = drawing_data.get("equipment_locations", [])
            pipe_routes = drawing_data.get("pipe_routes", [])
            if equipment or pipe_routes:
                drawing_info_text = """
【図面からの実データ】
"""
                if equipment:
                    drawing_info_text += f"確認された設備・機器:\n"
                    for eq in equipment[:15]:  # 最大15項目
                        drawing_info_text += f"- {eq}\n"
                if pipe_routes:
                    drawing_info_text += f"\n配管ルート情報:\n"
                    for route in pipe_routes[:5]:  # 最大5ルート
                        drawing_info_text += f"- {route}\n"

        # 仕様書テキストを取得
        spec_text = building_info.get("spec_text_excerpt", "")

        prompt = f"""あなたは熟練のガス設備積算技術者です。以下の仕様書からガス設備工事の見積項目を抽出してください。

【重要な制約】
1. **仕様書に明記されている項目**を中心に抽出してください
2. 数量が明記されていない場合は、下記の【数量推定ルール】に従って推定してください
3. 数量の推定根拠を estimation_basis フィールドに記載してください
4. 一式工事は quantity=1, unit="式" としてください

【数量推定ルール】（学校施設の標準値）
■ 配管延長の推定
  - 敷地内引込配管: 建物外周の1/4 + 建物〜境界線距離（通常30〜50m）
  - 屋内配管（白ガス管）: ガス栓数 × 平均配管長（15〜25m/栓）
  - 管径選定: 主管50A→分岐32A→枝管25A→末端15A（流量に応じて）

■ ガス機器数量の推定
  - ガス栓: 調理室（4〜8栓）、理科室（10〜20栓）、その他（1〜2栓/室）
  - ガスコンセント: ガス使用室 × 2個/室
  - 緊急遮断弁: ガス使用フロアに1台

■ 付帯工事の推定
  - 配管支持金物: 配管延長 ÷ 1.5m（支持間隔）
  - 貫通スリーブ: 壁・床貫通箇所数（図面から読取り、不明時は配管経路数×2）
  - 穴補修: 貫通箇所数と同数

■ 信頼度スコアの基準
  - 1.0: 仕様書に数量・仕様が明記
  - 0.8-0.9: 図面から読取り可能
  - 0.6-0.7: 上記ルールで推定
  - 0.5以下: 概算（要確認）

【仕様書の内容】
{spec_text[:15000] if spec_text else '仕様書テキストなし'}

【建物情報（参考）】
{building_summary}
{spec_table_info}
{drawing_info_text}

【抽出対象カテゴリ】
- 配管工事（白ガス管、カラー鋼管、PE管等）
- ガス栓・機器（ガスコンセント、ネジコック等）
- 付帯工事（撤去、穴補修、試験等）
- 経費（諸経費、運搬費等）

【重要】項目名は以下のKB標準名称を参考にしてください（マッチング精度向上のため）：
- 配管: 白ガス管、カラー鋼管、PE管、ガス配管、ガス管
- ガス栓: ガス栓、ガスコンセント、ネジコック、分岐コック
- 機器: ガス警報器、ガスメーター、ガス給湯器、ガスコンロ
- 工事: 撤去、配管撤去、穴補修、穴あけ、貫通、貫通部シール、はつり、埋戻し
- 付帯: 支持材、支持金物、配管支持、高所作業車、運搬、搬入
- 経費: 諸経費、現場管理費、気密試験

【出力形式】
JSON配列で、階層構造を持った見積項目を出力してください：

```json
[
  {{
    "item_no": "1",
    "level": 0,
    "name": "都市ガス設備工事",
    "specification": "",
    "quantity": 1,
    "unit": "式",
    "unit_price": null,
    "amount": null,
    "cost_type": "一式",
    "remarks": "",
    "confidence": 1.0
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "基本工事費",
    "specification": "",
    "quantity": 1,
    "unit": "式",
    "unit_price": null,
    "amount": null,
    "cost_type": "施工費",
    "remarks": "図面作成、申請業務、現場管理",
    "confidence": 0.9,
    "estimation_basis": "建物規模から標準的な基本工事費を算定"
  }},
  {{
    "item_no": "",
    "level": 1,
    "name": "配管工事費",
    "specification": "",
    "quantity": 1,
    "unit": "式",
    "unit_price": null,
    "amount": null,
    "cost_type": "材料費",
    "remarks": "",
    "confidence": 0.85
  }},
  {{
    "item_no": "",
    "level": 2,
    "name": "白ガス管",
    "specification": "15A",
    "quantity": 100,
    "unit": "m",
    "unit_price": null,
    "amount": null,
    "cost_type": "材料費",
    "remarks": "",
    "confidence": 0.7,
    "estimation_basis": "建物面積2,145㎡、ガス使用箇所9箇所から推定"
  }},
  {{
    "item_no": "",
    "level": 2,
    "name": "ガス栓",
    "specification": "",
    "quantity": 9,
    "unit": "箇所",
    "unit_price": null,
    "amount": null,
    "cost_type": "材料費",
    "remarks": "",
    "confidence": 0.9,
    "estimation_basis": "諸元表記載のガス栓数"
  }}
]
```

【重要】
- 数量は必ず数値で指定してください（nullではなく、推定値でも可）
- 単価はnullのままで構いません（後でKBから取得します）
- 仕様書にガス設備の記載がない場合は空配列 [] を返してください"""

        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=8000,
            temperature=0,  # 決定的に（毎回同じ結果）
            messages=[{"role": "user", "content": prompt}]
        )

        # コスト記録
        record_cost(
            operation="ガス設備見積生成",
            model_name=self.model_name,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            metadata={"source": "generate_detailed_estimate_items", "discipline": "ガス設備工事"}
        )

        response_text = response.content[0].text
        logger.debug(f"LLM Response for gas: {response_text[:500]}...")

        # 堅牢なJSON抽出を使用
        items_data = extract_json_array_robust(response_text)
        logger.info(f"Gas items extracted: {len(items_data)} items")

        # EstimateItemに変換
        estimate_items = []
        for item_data in items_data:
            # cost_typeの変換
            cost_type_str = item_data.get("cost_type", "")
            cost_type = None
            if cost_type_str:
                for ct in CostType:
                    if ct.value == cost_type_str:
                        cost_type = ct
                        break

            estimate_item = EstimateItem(
                item_no=item_data.get("item_no", ""),
                level=item_data.get("level", 0),
                name=item_data.get("name", ""),
                specification=item_data.get("specification", ""),
                quantity=item_data.get("quantity"),
                unit=item_data.get("unit", ""),
                unit_price=item_data.get("unit_price"),
                amount=item_data.get("amount"),
                discipline=DisciplineType.GAS,
                cost_type=cost_type,
                remarks=item_data.get("remarks", ""),
                source_type="ai_generated",
                source_reference=item_data.get("estimation_basis", "AI設計"),
                confidence=item_data.get("confidence", 0.7)
            )

            estimate_items.append(estimate_item)

        return estimate_items

    def generate_detailed_items_for_electrical(
        self,
        building_info: Dict[str, Any]
    ) -> List[EstimateItem]:
        """
        電気設備の詳細見積項目をAI生成（仕様書準拠版）

        仕様書に記載された内容のみを抽出し、過剰な項目生成を防ぎます。
        """
        logger.info("Generating detailed electrical equipment items (specification-based)")

        # 仕様書テキストを取得
        spec_text = building_info.get("spec_text_excerpt", "")
        if not spec_text:
            logger.warning("No specification text available - using minimal generation")

        # 建物情報を簡潔に抽出
        bldg = building_info.get("building_info", {})
        elec = building_info.get("facility_requirements", {}).get("electrical", {})
        legal_standards = building_info.get("legal_standards", [])

        # 諸元表情報を取得
        spec_table = building_info.get("spec_table", {})
        rooms_info = spec_table.get("rooms", [])
        equipment_summary = spec_table.get("equipment_summary", {})
        total_outlets = equipment_summary.get("total_outlets", 0)
        total_rooms = equipment_summary.get("total_rooms", 0)

        # 仕様書準拠のプロンプト
        prompt = f"""あなたは熟練の電気設備積算技術者です。以下の仕様書と建物情報から電気設備工事の見積項目を生成してください。

【重要な制約】
1. 仕様書に明記されている項目を優先的に抽出
2. 仕様書に詳細がない場合は、下記の【数量推定ルール】に従って推定
3. 一式工事は quantity=1, unit="式" としてください
4. **必ず25項目以上**を生成してください

【数量推定ルール】（学校施設の標準値）
■ 電気容量の目安
  - 学校施設: 50〜80 VA/㎡（空調電気式の場合は100 VA/㎡）
  - キュービクル容量: 延床面積(㎡) × 60 VA/㎡ ÷ 0.6（需要率）→ kVA換算

■ 受変電設備
  - キュービクル: 1台（500kVA未満は1面、以上は2面）
  - 高圧ケーブル: 引込柱〜キュービクル間（通常30〜100m）
  - PAS（気中開閉器）: 1台

■ 幹線・分電盤
  - 分電盤: 2面/階 × 階数（電灯用・動力用）
  - 動力盤: 1面/階 × 階数
  - 幹線ケーブル: キュービクル〜各分電盤（垂直30m/階 + 水平20m/面）

■ 照明設備
  - LED照明器具: 床面積 ÷ 8㎡/台（教室は6㎡/台）
  - 非常照明: 部屋数 × 1台
  - 誘導灯: 4台/階 × 階数

■ コンセント・配線
  - コンセント: 床面積 ÷ 5㎡/箇所（教室は8箇所/室）
  - 照明配線: 照明器具数 × 5m
  - コンセント配線: コンセント数 × 8m

■ 弱電設備
  - LAN: 2口/室 × 部屋数
  - 電話: 1口/室 × 部屋数
  - 放送設備: 1式（校舎に1システム）

■ 防災設備
  - 感知器: 床面積 ÷ 60㎡/個（煙感知器）
  - 非常放送スピーカー: 1台/室

■ 信頼度スコアの基準
  - 1.0: 仕様書に数量・仕様が明記
  - 0.8-0.9: 図面から読取り可能
  - 0.6-0.7: 上記ルールで推定
  - 0.5以下: 概算（要確認）

【仕様書の内容】
{spec_text[:12000]}

【建物基本情報】
- 工事名: {building_info.get('project_name', '')}
- 延床面積: {bldg.get('total_floor_area', 2000)}㎡
- 階数: {bldg.get('floors', 3)}階
- 部屋数: {total_rooms or bldg.get('num_rooms', 30)}室
- コンセント数: {total_outlets or 200}箇所（推定）
- 用途: 学校（仮設校舎）

【生成すべきカテゴリと項目例】
1. 受変電設備（キュービクル、変圧器、高圧引込ケーブル）
2. 幹線・分電盤（分電盤、動力盤、幹線ケーブル、配線用遮断器）
3. 照明設備（LED照明器具、蛍光灯、誘導灯、非常照明、外灯）
4. コンセント設備（一般コンセント、専用コンセント、OAフロア配線）
5. 弱電設備（LAN配線、電話配線、放送設備、インターホン）
6. 防災設備（自動火災報知器、感知器、非常放送、避雷設備）
7. 付帯工事（電気試験調整、撤去工事、諸経費）

【出力形式】JSON配列（```不要）：
[
  {{"level": 1, "name": "受変電設備", "specification": "", "quantity": 1, "unit": "式", "cost_type": "施工費", "confidence": 0.8, "source": "推定"}},
  {{"level": 2, "name": "キュービクル", "specification": "屋外型 300kVA", "quantity": 1, "unit": "台", "cost_type": "機器費", "confidence": 0.7, "source": "推定"}},
  {{"level": 2, "name": "高圧引込ケーブル", "specification": "CV 38sq", "quantity": 50, "unit": "m", "cost_type": "材料費", "confidence": 0.6, "source": "推定"}}
]

学校施設に必要な標準的な電気設備を漏れなく生成してください。"""

        all_items = []

        # 親項目を追加
        parent_item = EstimateItem(
            item_no="1",
            level=0,
            name="電気設備工事",
            specification="",
            quantity=None,
            unit="式",
            unit_price=None,
            amount=None,
            discipline=DisciplineType.ELECTRICAL,
            cost_type=CostType.LUMP_SUM,
            remarks="",
            source_type="ai_generated",
            confidence=1.0
        )
        all_items.append(parent_item)

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=8000,
                temperature=0,  # 決定的に（毎回同じ結果）
                messages=[{"role": "user", "content": prompt}]
            )

            record_cost(
                operation="電気設備生成（仕様書準拠）",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={"source": "generate_electrical_spec_based"}
            )

            response_text = response.content[0].text
            logger.debug(f"LLM Response for electrical (first 500 chars): {response_text[:500]}")

            # 堅牢なJSON抽出を使用
            items_data = extract_json_array_robust(response_text)
            logger.info(f"Electrical items extracted: {len(items_data)} items")

            # EstimateItemに変換
            for item_data in items_data:
                cost_type = None
                cost_type_str = item_data.get("cost_type", "")
                for ct in CostType:
                    if ct.value == cost_type_str:
                        cost_type = ct
                        break

                estimate_item = EstimateItem(
                    item_no="",
                    level=item_data.get("level", 2),
                    name=item_data.get("name", ""),
                    specification=item_data.get("specification", ""),
                    quantity=item_data.get("quantity"),
                    unit=item_data.get("unit", ""),
                    unit_price=None,
                    amount=None,
                    discipline=DisciplineType.ELECTRICAL,
                    cost_type=cost_type,
                    remarks=item_data.get("remarks", ""),
                    source_type="ai_generated",
                    source_reference=item_data.get("source", "仕様書"),
                    confidence=item_data.get("confidence", 0.7)
                )
                all_items.append(estimate_item)

            logger.info(f"Generated {len(items_data)} electrical items from specification")

        except Exception as e:
            logger.error(f"Failed to generate electrical items: {e}")

        # フォールバック: 項目が少ない場合は標準項目を追加
        if len(all_items) < 5:
            logger.warning(f"Only {len(all_items)} electrical items generated, adding standard items")
            all_items.extend(self._get_standard_electrical_items(building_info))

        logger.info(f"Total electrical items: {len(all_items)}")
        return all_items

    def _get_standard_electrical_items(self, building_info: Dict[str, Any]) -> List[EstimateItem]:
        """電気設備の標準項目を生成（フォールバック用）"""
        bldg = building_info.get("building_info", {})
        floor_area = bldg.get("total_floor_area", 2000)
        num_floors = bldg.get("floors", 3)
        num_rooms = bldg.get("num_rooms", 30)

        standard_items = [
            # 受変電設備
            {"level": 1, "name": "受変電設備", "spec": "", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "キュービクル", "spec": "屋外型", "qty": 1, "unit": "台", "cost_type": "機器費"},
            {"level": 2, "name": "高圧引込ケーブル", "spec": "CV 38sq", "qty": 30, "unit": "m", "cost_type": "材料費"},
            # 幹線・分電盤
            {"level": 1, "name": "幹線・分電盤設備", "spec": "", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "分電盤", "spec": "主幹100A", "qty": num_floors * 2, "unit": "面", "cost_type": "機器費"},
            {"level": 2, "name": "動力盤", "spec": "主幹60A", "qty": num_floors, "unit": "面", "cost_type": "機器費"},
            {"level": 2, "name": "幹線ケーブル", "spec": "CV 38sq-3C", "qty": int(floor_area * 0.05), "unit": "m", "cost_type": "材料費"},
            # 照明設備
            {"level": 1, "name": "照明設備", "spec": "", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "LED照明器具", "spec": "40W相当", "qty": int(floor_area / 10), "unit": "台", "cost_type": "材料費"},
            {"level": 2, "name": "誘導灯", "spec": "B級両面", "qty": num_floors * 4, "unit": "台", "cost_type": "材料費"},
            {"level": 2, "name": "非常照明", "spec": "LED 30分型", "qty": num_rooms, "unit": "台", "cost_type": "材料費"},
            # コンセント設備
            {"level": 1, "name": "コンセント設備", "spec": "", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "コンセント", "spec": "2P15A", "qty": int(floor_area / 5), "unit": "箇所", "cost_type": "材料費"},
            {"level": 2, "name": "コンセント配線", "spec": "VVF 2.0-2C", "qty": int(floor_area * 0.3), "unit": "m", "cost_type": "材料費"},
            # 弱電設備
            {"level": 1, "name": "弱電設備", "spec": "", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "LAN配線", "spec": "Cat6", "qty": num_rooms * 2, "unit": "箇所", "cost_type": "材料費"},
            {"level": 2, "name": "放送設備", "spec": "校内放送", "qty": 1, "unit": "式", "cost_type": "施工費"},
            # 防災設備
            {"level": 1, "name": "防災設備", "spec": "", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "自動火災報知器", "spec": "P型1級", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "感知器", "spec": "煙感知器", "qty": num_rooms * 2, "unit": "個", "cost_type": "材料費"},
            # 付帯工事
            {"level": 1, "name": "付帯工事", "spec": "", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "電気試験調整", "spec": "", "qty": 1, "unit": "式", "cost_type": "施工費"},
            {"level": 2, "name": "諸経費", "spec": "", "qty": 1, "unit": "式", "cost_type": "諸経費"},
        ]

        items = []
        for item_data in standard_items:
            cost_type = None
            for ct in CostType:
                if ct.value == item_data.get("cost_type", ""):
                    cost_type = ct
                    break

            items.append(EstimateItem(
                item_no="",
                level=item_data["level"],
                name=item_data["name"],
                specification=item_data.get("spec", ""),
                quantity=item_data.get("qty"),
                unit=item_data.get("unit", ""),
                unit_price=None,
                amount=None,
                discipline=DisciplineType.ELECTRICAL,
                cost_type=cost_type,
                remarks="標準項目",
                source_type="ai_generated",
                source_reference="標準仕様",
                confidence=0.6
            ))

        logger.info(f"Added {len(items)} standard electrical items as fallback")
        return items

    def generate_detailed_items_for_mechanical(
        self,
        building_info: Dict[str, Any]
    ) -> List[EstimateItem]:
        """
        機械設備の詳細見積項目をAI生成（仕様書準拠版）

        仕様書に記載された内容のみを抽出し、過剰な項目生成を防ぎます。
        """
        logger.info("Generating detailed mechanical equipment items (specification-based)")

        # 仕様書テキストを取得
        spec_text = building_info.get("spec_text_excerpt", "")
        if not spec_text:
            logger.warning("No specification text available - using minimal generation")

        # 建物情報を簡潔に抽出
        bldg = building_info.get("building_info", {})
        mech = building_info.get("facility_requirements", {}).get("mechanical", {})
        legal_standards = building_info.get("legal_standards", [])

        # 仕様書準拠のプロンプト
        prompt = f"""あなたは熟練の機械設備積算技術者です。以下の仕様書から機械設備工事の見積項目を抽出してください。

【重要な制約】
1. **仕様書に明記されている項目**を中心に抽出してください
2. 数量が明記されていない場合は、下記の【数量推定ルール】に従って推定してください
3. 数量の推定根拠を estimation_basis フィールドに記載してください
4. 一式工事は quantity=1, unit="式" としてください
5. **必ず20項目以上**を生成してください

【数量推定ルール】（学校施設の標準値）
■ 空調設備
  - 空調負荷: 学校施設は100〜150 W/㎡（冷房）、80〜120 W/㎡（暖房）
  - パッケージエアコン: 1台/教室（4.0〜5.6kW）、1台/管理室（2.8〜4.0kW）
  - 室外機: 室内機と1:1または1:2〜4（マルチエアコン）
  - 冷媒配管: 室内機〜室外機間（平均15〜30m/組）

■ 換気設備
  - 換気回数: 教室6回/h、トイレ10回/h、廊下3回/h
  - 換気扇: トイレ・更衣室・給湯室に各1台
  - 全熱交換器: 教室に1台/室（省エネ仕様の場合）

■ 給排水設備
  - 給水配管: 衛生器具数 × 3m + 水平距離
  - 排水配管: 衛生器具数 × 4m + 水平距離
  - 給水ポンプ: 3階以上または受水槽方式で1台

■ 衛生器具（学校標準）
  - 大便器: 男子1器/60人、女子1器/30人
  - 小便器: 男子1器/30人
  - 洗面器: 2器/トイレ1箇所
  - 流し台: 調理室・給湯室に各1台

■ 信頼度スコアの基準
  - 1.0: 仕様書に数量・仕様が明記
  - 0.8-0.9: 図面から読取り可能
  - 0.6-0.7: 上記ルールで推定
  - 0.5以下: 概算（要確認）

【仕様書の内容】
{spec_text[:15000]}

【建物基本情報（参考）】
- 工事名: {building_info.get('project_name', '')}
- 延床面積: {bldg.get('total_floor_area', '')}㎡
- 階数: {bldg.get('floors', '')}階

【抽出対象カテゴリ】
- 空調設備（エアコン、室外機、冷媒配管等）
- 換気設備（換気扇、全熱交換器、ダクト、制気口等）
- 給排水設備（給水管、排水管、ポンプ、受水槽等）
- 衛生器具（便器、洗面器、流し台、給湯器等）
- 付帯工事（保温工事、撤去、試運転、諸経費等）

【出力形式】JSON配列のみ（```不要）：
[
  {{"level": 1, "name": "カテゴリ名", "specification": "", "quantity": null, "unit": "式", "cost_type": "施工費", "confidence": 0.9, "source": "仕様書P○"}},
  {{"level": 2, "name": "項目名", "specification": "仕様", "quantity": 数量, "unit": "単位", "cost_type": "材料費", "confidence": 0.8, "source": "仕様書P○"}}
]

仕様書に機械設備に関する記載がない場合は [] を返してください。"""

        all_items = []

        # 親項目を追加
        parent_item = EstimateItem(
            item_no="1",
            level=0,
            name="機械設備工事",
            specification="",
            quantity=None,
            unit="式",
            unit_price=None,
            amount=None,
            discipline=DisciplineType.MECHANICAL,
            cost_type=CostType.LUMP_SUM,
            remarks="",
            source_type="ai_generated",
            confidence=1.0
        )
        all_items.append(parent_item)

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=8000,
                temperature=0,  # 決定的に（毎回同じ結果）
                messages=[{"role": "user", "content": prompt}]
            )

            record_cost(
                operation="機械設備生成（仕様書準拠）",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={"source": "generate_mechanical_spec_based"}
            )

            response_text = response.content[0].text
            logger.debug(f"LLM Response for mechanical (first 500 chars): {response_text[:500]}")

            # 堅牢なJSON抽出を使用
            items_data = extract_json_array_robust(response_text)
            logger.info(f"Mechanical items extracted: {len(items_data)} items")

            # EstimateItemに変換
            for item_data in items_data:
                cost_type = None
                cost_type_str = item_data.get("cost_type", "")
                for ct in CostType:
                    if ct.value == cost_type_str:
                        cost_type = ct
                        break

                estimate_item = EstimateItem(
                    item_no="",
                    level=item_data.get("level", 2),
                    name=item_data.get("name", ""),
                    specification=item_data.get("specification", ""),
                    quantity=item_data.get("quantity"),
                    unit=item_data.get("unit", ""),
                    unit_price=None,
                    amount=None,
                    discipline=DisciplineType.MECHANICAL,
                    cost_type=cost_type,
                    remarks=item_data.get("remarks", ""),
                    source_type="ai_generated",
                    source_reference=item_data.get("source", "仕様書"),
                    confidence=item_data.get("confidence", 0.7)
                )
                all_items.append(estimate_item)

            logger.info(f"Generated {len(items_data)} mechanical items from specification")

        except Exception as e:
            logger.error(f"Failed to generate mechanical items: {e}")

        logger.info(f"Total mechanical items: {len(all_items)}")
        return all_items

    def _normalize_text(self, text: str) -> str:
        """テキストを正規化（空白・記号を統一、類義語統一）"""
        if not text:
            return ""
        import re
        # 全角→半角
        text = text.replace('（', '(').replace('）', ')').replace('　', ' ')
        # 記号の統一
        text = text.replace('・', '').replace('/', '').replace('-', '')
        # 複数空白を1つに
        text = re.sub(r'\s+', ' ', text)
        text = text.strip().lower()

        # 接尾辞の統一（「工事」「費」「材」等を除去して比較しやすく）
        suffixes_to_remove = ['工事', '費', '工', '材料', '材']
        for suffix in suffixes_to_remove:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[:-len(suffix)]

        # 類義語の統一
        synonyms = {
            '穴補修': '穴補修',
            '穴あけ': '穴補修',
            '壁穿孔': '穴補修',
            '貫通': '穴補修',
            '撤去': '撤去',
            '解体': '撤去',
            '取り外し': '撤去',
            '取外し': '撤去',
            '取付': '取付',
            '設置': '取付',
            '据付': '取付',
        }
        for key, value in synonyms.items():
            if key in text:
                text = text.replace(key, value)

        return text

    def _extract_size(self, text: str) -> str:
        """テキストからサイズ情報を抽出（例: 15A, 20mm）"""
        if not text:
            return ""
        import re
        # サイズパターン: 数値 + 単位（A, mm, cm等）
        match = re.search(r'(\d+)\s*([Aａmcm]{1,2})', text, re.IGNORECASE)
        if match:
            return f"{match.group(1)}{match.group(2).upper()}"
        return ""

    def _get_category(self, item_name: str) -> str:
        """項目名からカテゴリを抽出（例: 白ガス管、PE管）"""
        # カテゴリキーワード
        categories = [
            "白ガス管", "カラー鋼管", "PE管", "露出結び",
            "ガスコンセント", "ネジコック", "分岐コック",
            "ボールスライドジョイント", "ガスメーター",
            "配管支持金具", "穴あけ", "埋戻し", "コンクリート",
            "高所作業車", "運搬", "諸経費", "試験", "検査", "撤去"
        ]

        for category in categories:
            if category in item_name:
                return category
        return ""

    def enrich_with_prices(self, estimate_items: List[EstimateItem]) -> List[EstimateItem]:
        """
        KBから単価を取得して項目に付与（ベクトル検索 + フォールバック版）

        Args:
            estimate_items: 単価未設定の見積項目リスト

        Returns:
            単価・金額が設定された見積項目リスト
        """
        vector_search_available = self.vector_search and self.vector_search.is_available()
        logger.info(f"Enriching {len(estimate_items)} items with prices from KB "
                   f"({len(self.price_kb)} KB items, vector_search={vector_search_available})")

        enriched_items = []
        vector_match_count = 0
        string_match_count = 0

        for item in estimate_items:
            # 親項目（level 0）のみスキップ - 数量nullでも単価マッチングは試行
            if item.level == 0:
                enriched_items.append(item)
                continue

            # テキストを正規化
            item_name_norm = self._normalize_text(item.name)
            item_spec_norm = self._normalize_text(item.specification or "")
            item_size = self._extract_size(item.specification or "")
            item_category = self._get_category(item.name)

            logger.debug(f"Matching: '{item.name}' {item.specification} | discipline={item.discipline.value}")

            # ===== Phase 3: ベクトル検索を最初に試行 =====
            matched_item = None
            match_type = ""
            best_score = 0.0

            if vector_search_available:
                vector_result = self._vector_search_match(
                    item.name,
                    item.specification or "",
                    item.discipline.value
                )
                if vector_result:
                    kb_item = vector_result["kb_item"]
                    # 単位互換性チェック
                    if self._check_unit_compatibility(item.unit, kb_item.get("unit", "")):
                        # 単価妥当性チェック
                        if self._validate_price(item.name, kb_item.get("unit_price")):
                            matched_item = kb_item
                            match_type = "vector"
                            best_score = vector_result["score"]
                            vector_match_count += 1
                            logger.debug(f"✓ Vector match: '{item.name}' → '{kb_item.get('item_id')}' "
                                       f"(score={best_score:.3f})")

            # ===== フォールバック: 文字列マッチング =====
            if not matched_item:
                # KBから類似項目を検索
                best_match = None
                category_fallback = None
                category_fallback_score = 0.0
                kb_candidates = 0

                # Phase 2: 類義語を取得
                item_synonyms = self._find_synonyms(item.name)
                item_synonyms_norm = [self._normalize_text(s) for s in item_synonyms]

                for kb_item in self.price_kb:
                    # Phase 2: 工事区分の互換性チェック（緩和版）
                    kb_discipline = kb_item.get("discipline", "")
                    if not self._is_discipline_compatible(kb_discipline, item.discipline.value):
                        continue

                    kb_candidates += 1

                    kb_desc = kb_item.get("description", "")
                    kb_spec = kb_item.get("features", {}).get("specification", "")
                    kb_full_text = f"{kb_desc} {kb_spec}"

                    # 正規化
                    kb_desc_norm = self._normalize_text(kb_desc)
                    kb_spec_norm = self._normalize_text(kb_spec)
                    kb_full_norm = self._normalize_text(kb_full_text)
                    kb_size = self._extract_size(kb_spec)
                    kb_category = self._get_category(kb_desc)

                    # KB側の類義語も取得
                    kb_synonyms = self._find_synonyms(kb_desc)
                    kb_synonyms_norm = [self._normalize_text(s) for s in kb_synonyms]

                    # 詳細な類似度計算
                    score = 0.0

                    # 1. 項目名の一致（正規化後）- 類義語も考慮
                    if item_name_norm == kb_desc_norm:
                        score += 2.0  # 完全一致は高スコア
                    elif item_name_norm in kb_desc_norm or kb_desc_norm in item_name_norm:
                        score += 1.5
                    # Phase 2: 類義語でのマッチング
                    elif any(syn in kb_synonyms_norm for syn in item_synonyms_norm):
                        score += 1.8  # 類義語一致は高スコア
                        logger.debug(f"  Synonym match: {item.name} ↔ {kb_desc}")
                    elif any(word in kb_desc_norm for word in item_name_norm.split() if len(word) > 1):
                        score += 1.0

                    # 2. カテゴリの一致
                    if item_category and kb_category and item_category == kb_category:
                        score += 1.0
                        # カテゴリが一致する場合はフォールバック候補
                        if score > category_fallback_score:
                            category_fallback = kb_item
                            category_fallback_score = score

                    # 3. 仕様・サイズの一致
                    if item_spec_norm and kb_spec_norm:
                        # 完全一致
                        if item_spec_norm == kb_spec_norm:
                            score += 1.5
                        # サイズ一致（例: 15A）
                        elif item_size and kb_size and item_size == kb_size:
                            score += 1.2
                        # 仕様が含まれる
                        elif item_spec_norm in kb_full_norm or kb_spec_norm in item_spec_norm:
                            score += 0.8

                    # 4. 単位の一致
                    unit_match_score = 0.0
                    unit_compatible = True  # 単位の互換性フラグ

                    if item.unit == kb_item.get("unit"):
                        unit_match_score = 0.5
                    elif item.unit and kb_item.get("unit"):
                        # m と メートル、式 と 式 等
                        unit_norm_item = self._normalize_text(item.unit)
                        unit_norm_kb = self._normalize_text(kb_item.get("unit", ""))
                        if unit_norm_item == unit_norm_kb:
                            unit_match_score = 0.5
                        elif unit_norm_item in unit_norm_kb or unit_norm_kb in unit_norm_item:
                            unit_match_score = 0.3
                        else:
                            # 単位が完全に異なる場合は互換性なし（例: 式 vs 箇所）
                            incompatible_pairs = [
                                ("式", "箇所"), ("式", "個"), ("式", "m"), ("式", "台"),
                                ("箇所", "m"), ("個", "m"), ("台", "m"), ("ヶ所", "m")
                            ]
                            for u1, u2 in incompatible_pairs:
                                if (u1 in unit_norm_item and u2 in unit_norm_kb) or \
                                   (u2 in unit_norm_item and u1 in unit_norm_kb):
                                    unit_compatible = False
                                    break

                    # 単位が互換性ありの場合のみスコアに加算
                    if unit_compatible:
                        score += unit_match_score
                    else:
                        # 単位不整合の場合はマッチング対象外
                        score = 0
                        logger.debug(f"  ✗ Unit incompatible: {item.unit} vs {kb_item.get('unit')} - skipping")
                        continue

                    if score > best_score:
                        best_score = score
                        best_match = kb_item

                # マッチング成功（閾値を調整）
                logger.debug(f"  KB candidates: {kb_candidates}, best_score={best_score:.2f}")

                if best_match and best_score >= 1.0:
                    # 高品質マッチ（項目名+仕様が一致）
                    matched_item = best_match
                    match_type = "exact"
                    string_match_count += 1
                    logger.debug(f"✓ Exact match '{item.name}' → '{best_match.get('item_id')}' (score={best_score:.2f})")
                elif best_match and best_score >= 0.3:  # 閾値緩和: 0.5 → 0.3
                    # 中品質マッチ（項目名 or カテゴリが一致）
                    matched_item = best_match
                    match_type = "partial"
                    string_match_count += 1
                    logger.debug(f"≈ Partial match '{item.name}' → '{best_match.get('item_id')}' (score={best_score:.2f})")
                elif category_fallback and category_fallback_score >= 0.5:  # 閾値緩和: 0.8 → 0.5
                    # カテゴリフォールバック（カテゴリは一致するが仕様が異なる）
                    matched_item = category_fallback
                    match_type = "category"
                    string_match_count += 1
                    logger.debug(f"↳ Category fallback '{item.name}' → '{category_fallback.get('item_id')}' (score={category_fallback_score:.2f})")

            # マッチング失敗の場合
            if not matched_item:
                logger.warning(f"✗ No match for '{item.name}' {item.specification} (best={best_score:.2f})")

            if matched_item:
                # 最大スコア5.0として正規化（50%=2.5）
                normalized_score = min(best_score / 5.0, 1.0)
                confidence_pct = int(normalized_score * 100)

                # Phase 2: 単価妥当性チェック
                matched_price = matched_item.get("unit_price")
                price_valid = self._validate_price(item.name, matched_price)

                # 30%以上のマッチングで単価を適用（閾値緩和: 0.50 → 0.30）
                if (normalized_score >= 0.30 or best_score >= 0.5) and price_valid:
                    item.unit_price = matched_price
                    if item.quantity and item.unit_price:
                        item.amount = item.quantity * item.unit_price
                    item.confidence = normalized_score
                    logger.info(f"✓ Match applied ({confidence_pct}%): {item.name} → ¥{item.unit_price:,.0f}")
                elif not price_valid:
                    # 単価が妥当でない場合は適用しない
                    item.confidence = normalized_score * 0.5  # 信頼度を下げる
                    logger.warning(f"⚠ Price rejected ({confidence_pct}%): {item.name} - KB has ¥{matched_price:,.0f} but price validation failed")
                else:
                    # 75%未満は参考値として記録するが金額は空
                    item.confidence = normalized_score
                    logger.info(f"△ Low confidence ({confidence_pct}%): {item.name} - KB has ¥{matched_price:,.0f} but not applied")

                item.price_references = [matched_item.get("item_id")]
                item.source_reference = f"KB:{matched_item.get('item_id')}[{match_type}]({confidence_pct}%), {item.source_reference}"

            enriched_items.append(item)

        # 親項目の金額を子項目の合計で計算
        enriched_items = self._calculate_parent_amounts(enriched_items)

        # 階層的なナンバリングを割り当て
        enriched_items = self._assign_item_numbers(enriched_items)

        matched_count = sum(1 for item in enriched_items if item.unit_price is not None)
        if len(estimate_items) > 0:
            match_rate = matched_count/len(estimate_items)*100
            logger.info(f"Price matching: {matched_count}/{len(estimate_items)} items ({match_rate:.1f}%)")
            logger.info(f"  - Vector search matches: {vector_match_count}")
            logger.info(f"  - String matching matches: {string_match_count}")
        else:
            logger.warning("No items to match prices for")

        return enriched_items

    def _calculate_parent_amounts(self, items: List[EstimateItem]) -> List[EstimateItem]:
        """
        親項目の金額を子項目の合計で計算

        工事区分ごとにグループ化し、階層構造に基づいて金額を計算します。
        """
        if not items:
            return items

        # 工事区分ごとにグループ化
        discipline_groups = {}
        for item in items:
            disc = item.discipline.value if item.discipline else "その他"
            if disc not in discipline_groups:
                discipline_groups[disc] = []
            discipline_groups[disc].append(item)

        logger.debug(f"Processing {len(discipline_groups)} discipline groups for parent amounts")

        # 各工事区分ごとに処理
        for disc_name, disc_items in discipline_groups.items():
            # 逆順で処理（高いLevel→低いLevel）
            # まず最大Levelを取得
            max_level = max(item.level for item in disc_items)

            # 高いLevelから順に処理
            for target_level in range(max_level - 1, -1, -1):
                for i, item in enumerate(disc_items):
                    if item.level != target_level:
                        continue

                    # このアイテムの直下の子項目を探す（同じ工事区分内）
                    children_amount = 0
                    has_children = False

                    for j in range(i + 1, len(disc_items)):
                        child = disc_items[j]
                        # 同じか低いレベルに到達したら終了
                        if child.level <= target_level:
                            break
                        # 直接の子項目（level = target_level + 1）の金額を加算
                        if child.level == target_level + 1:
                            has_children = True
                            children_amount += child.amount or 0

                    # 子項目がある場合は金額を設定
                    if has_children and children_amount > 0:
                        item.amount = children_amount
                        item.unit_price = None  # 親項目は単価をクリア
                        logger.debug(f"  {disc_name} L{target_level} '{item.name}': ¥{children_amount:,.0f}")

            # 工事区分の合計をログ出力
            disc_total = sum(item.amount or 0 for item in disc_items if item.level == 0)
            logger.info(f"Discipline '{disc_name}' total: ¥{disc_total:,.0f}")

        return items

    def _assign_item_numbers(self, items: List[EstimateItem]) -> List[EstimateItem]:
        """
        階層構造に基づいてナンバリングを割り当て

        例: 1, 1.1, 1.1.1, 1.1.2, 1.2, 2, 2.1, ...

        工事区分ごとに独立してナンバリングします。
        """
        if not items:
            return items

        # 工事区分ごとにグループ化
        discipline_groups = {}
        for item in items:
            disc = item.discipline.value if item.discipline else "その他"
            if disc not in discipline_groups:
                discipline_groups[disc] = []
            discipline_groups[disc].append(item)

        # 各工事区分ごとにナンバリング
        for disc_name, disc_items in discipline_groups.items():
            # 各レベルの現在のカウンター
            # counters[level] = 現在の番号
            counters = {}
            # 各項目の番号スタック（親の番号を保持）
            number_stack = []

            for item in disc_items:
                level = item.level

                # 新しいレベルに入った場合、カウンターを初期化
                if level not in counters:
                    counters[level] = 0

                # 同じレベル以下に戻った場合、より深いレベルのカウンターをリセット
                levels_to_reset = [l for l in counters.keys() if l > level]
                for l in levels_to_reset:
                    counters[l] = 0

                # 現在のレベルのカウンターをインクリメント
                counters[level] += 1

                # 番号スタックを現在のレベルまで調整
                number_stack = number_stack[:level]
                number_stack.append(counters[level])

                # 番号文字列を生成（例: "1.2.3"）
                item_number = ".".join(str(n) for n in number_stack)
                item.item_no = item_number

            logger.debug(f"Assigned item numbers for {disc_name}: {len(disc_items)} items")

        return items

    def generate_estimate(
        self,
        spec_pdf_path: str,
        discipline: DisciplineType,
        legal_standards: list = None
    ) -> FMTDocument:
        """
        仕様書からAIで詳細見積を自動生成

        Args:
            spec_pdf_path: 仕様書PDFのパス
            discipline: 工事区分
            legal_standards: 適用法令リスト（例: ["建築基準法", "電気設備技術基準"]）

        Returns:
            生成されたFMTDocument
        """
        if legal_standards is None:
            legal_standards = []
        logger.info(f"Starting AI-based estimate generation for {discipline.value}")
        if legal_standards:
            logger.info(f"Applicable legal standards: {', '.join(legal_standards)}")

        # 1. 仕様書からテキスト抽出
        spec_text = self.extract_text_from_pdf(spec_pdf_path)

        # 2. 建物情報を詳細抽出
        building_info = self.extract_building_info(spec_text)

        # 仕様書テキストを追加（生成時に参照するため）
        # 最初の30000文字のみ（トークン制限のため）
        building_info["spec_text_excerpt"] = spec_text[:30000]

        # 法令情報を追加
        if legal_standards:
            building_info["legal_standards"] = legal_standards

        # 2.5. 諸元表から詳細な部屋・設備情報を抽出（テキストベース）
        spec_table_data = self.extract_specification_tables(spec_pdf_path, spec_text)
        if spec_table_data.get("rooms"):
            # 諸元表データを building_info にマージ
            building_info["spec_table"] = spec_table_data
            equipment_summary = spec_table_data.get("equipment_summary", {})
            if equipment_summary.get("total_gas_outlets"):
                building_info.setdefault("facility_requirements", {}).setdefault("gas", {})["num_connection_points"] = equipment_summary["total_gas_outlets"]
            if equipment_summary.get("total_rooms"):
                building_info.setdefault("building_info", {})["num_rooms"] = equipment_summary["total_rooms"]
            logger.info(f"Merged spec table data: {len(spec_table_data.get('rooms', []))} rooms, {equipment_summary.get('total_gas_outlets', 0)} gas outlets")

        # 2.6. Phase 1: Vision抽出による諸元表データ取得（より正確）
        if HAS_PYMUPDF:
            vision_table_data = self.extract_specification_table_with_vision(spec_pdf_path)
            if vision_table_data.get("rooms"):
                # Vision抽出データで上書き・補完
                building_info["spec_table_vision"] = vision_table_data
                totals = vision_table_data.get("totals", {})

                # Vision抽出結果でより正確な値を上書き
                if totals.get("room_count"):
                    building_info.setdefault("building_info", {})["num_rooms"] = totals["room_count"]
                if totals.get("gas_outlet_total"):
                    building_info.setdefault("facility_requirements", {}).setdefault("gas", {})["num_connection_points"] = totals["gas_outlet_total"]
                if totals.get("electrical_outlet_total"):
                    building_info.setdefault("facility_requirements", {}).setdefault("electrical", {})["outlet_count"] = totals["electrical_outlet_total"]

                logger.info(f"Vision extraction merged: {totals.get('room_count', 0)} rooms, "
                           f"{totals.get('gas_outlet_total', 0)} gas outlets, "
                           f"{totals.get('electrical_outlet_total', 0)} electrical outlets")

        # 2.7. 図面から設備情報を抽出（オプション）
        if HAS_PYMUPDF:
            drawing_info = self.extract_drawing_info(spec_pdf_path)
            if drawing_info.get("equipment_locations") or drawing_info.get("pipe_routes"):
                building_info["drawing_info"] = drawing_info
                logger.info(f"Merged drawing data: {len(drawing_info.get('equipment_locations', []))} equipment items, {len(drawing_info.get('pipe_routes', []))} pipe routes")

        # 3. 工事区分別に詳細項目を生成
        logger.info(f"Starting item generation for discipline: {discipline.value}")
        if discipline == DisciplineType.GAS:
            logger.info(f"ガス設備のAI自動生成を開始")
            estimate_items = self.generate_detailed_items_for_gas(building_info)
            logger.info(f"ガス設備: {len(estimate_items)}項目を生成")
        elif discipline == DisciplineType.ELECTRICAL:
            logger.info(f"電気設備のAI自動生成を開始")
            estimate_items = self.generate_detailed_items_for_electrical(building_info)
            logger.info(f"電気設備: {len(estimate_items)}項目を生成")
        elif discipline == DisciplineType.MECHANICAL:
            logger.info(f"機械設備のAI自動生成を開始")
            estimate_items = self.generate_detailed_items_for_mechanical(building_info)
            logger.info(f"機械設備: {len(estimate_items)}項目を生成")
        else:
            logger.warning(f"{discipline.value} is not yet implemented")
            estimate_items = []

        # 3.5. チェックリストで項目網羅性を検証・補完
        checker = EstimationChecker()
        floor_area = building_info.get("building_info", {}).get("total_floor_area", 0) or 0
        num_rooms = building_info.get("building_info", {}).get("num_rooms", 0) or 0

        # チェックリストとの比較
        coverage = checker.check_item_coverage(estimate_items, discipline)
        logger.info(f"Checklist coverage for {discipline.value}: {coverage['coverage_rate']*100:.1f}% "
                   f"({coverage['covered_count']}/{coverage['total_check_items']} items)")

        # 不足項目を追加（カバー率が70%未満の場合）
        if coverage['coverage_rate'] < 0.7 and coverage['missing_items']:
            missing_items = checker.generate_missing_items(
                estimate_items, discipline, floor_area, num_rooms
            )
            if missing_items:
                # 適切な親項目の下に追加
                estimate_items.extend(missing_items)
                logger.info(f"Added {len(missing_items)} missing items from checklist")

        # 3.6. 数量が未設定の項目に推定数量を設定
        estimate_items = checker.estimate_quantities(
            estimate_items, discipline, floor_area, num_rooms
        )

        # 4. KBから単価を取得
        estimate_items = self.enrich_with_prices(estimate_items)

        # 5. FMTDocumentを作成
        # contract_periodが辞書の場合は文字列に変換
        contract_period = building_info.get("contract_period", "")
        if isinstance(contract_period, dict):
            # 辞書の場合、値を結合して文字列化
            if "construction_period" in contract_period:
                contract_period = contract_period["construction_period"]
            elif "rental_period" in contract_period:
                contract_period = contract_period["rental_period"]
            else:
                contract_period = str(contract_period)

        project_info = ProjectInfo(
            project_name=building_info.get("project_name", ""),
            client_name=building_info.get("client_name", ""),
            location=building_info.get("location", ""),
            contract_period=contract_period,
            payment_terms="本紙記載内容のみ有効とする。",
            floor_area_m2=building_info.get("building_info", {}).get("total_floor_area"),
            num_rooms=building_info.get("building_info", {}).get("num_rooms")
        )

        # 5.5. 妥当性検証
        unit_price_check = checker.validate_unit_price(
            estimate_items, discipline, "学校", floor_area
        )
        logger.info(f"Unit price validation: {unit_price_check['message']}")

        fmt_doc = FMTDocument(
            created_at=datetime.now().isoformat(),
            project_info=project_info,
            facility_type=FacilityType.SCHOOL,
            disciplines=[discipline],
            estimate_items=estimate_items,
            metadata={
                "payment_terms": "本紙記載内容のみ有効とする。",
                "remarks": "法定福利費を含む。",
                "source": "AI自動生成",
                "building_info": building_info.get("building_info", {}),
                "checklist_coverage": coverage,
                "unit_price_check": unit_price_check,
            }
        )

        logger.info(f"Generated FMTDocument with {len(estimate_items)} items")
        return fmt_doc

    def generate_detailed_items_generic(
        self, building_info: Dict[str, Any], discipline: DisciplineType
    ) -> List[EstimateItem]:
        """
        汎用的な設備項目生成メソッド（空調・衛生・消防等に対応）

        Args:
            building_info: 建物情報
            discipline: 工事区分

        Returns:
            見積項目リスト
        """
        spec_text = building_info.get("spec_text_excerpt", "")[:30000]
        discipline_name = discipline.value

        # KBから該当カテゴリの項目例を取得
        kb_examples = []
        for kb_item in self.price_kb[:50]:  # 最初の50項目
            if kb_item.get("discipline") == discipline_name:
                kb_examples.append(f"- {kb_item.get('description')} ({kb_item.get('unit')})")
        kb_examples_str = "\n".join(kb_examples[:20]) if kb_examples else "（KB項目なし）"

        prompt = f"""あなたは熟練の建築設備積算技術者です。以下の仕様書から「{discipline_name}」に関する見積項目を抽出してください。

【重要な制約】
1. **仕様書に明記されている項目**を中心に抽出してください
2. 数量が明記されていない場合は、建物規模（面積、部屋数等）から合理的に推定してください
3. 数量の推定根拠を estimation_basis フィールドに記載してください
4. 推定数量は控えめに設定してください（過大見積を避ける）
5. 一式工事は quantity=1, unit="式" としてください

【単価データベースの{discipline_name}項目例】
{kb_examples_str}

【建物情報】
- 工事名: {building_info.get('project_name', '不明')}
- 延床面積: {building_info.get('building_info', {}).get('total_floor_area', '不明')}㎡
- 階数: {building_info.get('building_info', {}).get('floors', '不明')}
- 部屋数: {building_info.get('building_info', {}).get('num_rooms', '不明')}

【仕様書（抜粋）】
{spec_text}

【出力形式】
JSON配列形式で出力してください：
```json
[
  {{
    "item_no": "1",
    "name": "項目名",
    "specification": "仕様",
    "quantity": 数量または null,
    "unit": "単位",
    "level": 1-2,
    "parent_item": "親項目名または null",
    "confidence": 0.0-1.0,
    "estimation_basis": "仕様書記載/図面参照"
  }}
]
```

仕様書を確認し、{discipline_name}に該当する項目のみを抽出してください。該当がなければ [] を返してください。"""

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=8000,
                temperature=0,  # 決定的に（毎回同じ結果）
                messages=[{"role": "user", "content": prompt}]
            )

            record_cost(
                operation=f"{discipline_name}項目生成",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={"source": "generate_detailed_items_generic", "discipline": discipline_name}
            )

            response_text = response.content[0].text

            # JSON抽出
            json_start = response_text.find('[')
            json_end = response_text.rfind(']') + 1

            if json_start == -1 or json_end <= json_start:
                logger.info(f"No items found for {discipline_name}")
                return []

            json_str = response_text[json_start:json_end]

            try:
                items_data = json.loads(json_str)
            except json.JSONDecodeError:
                json_str = repair_json_array(json_str)
                items_data = json.loads(json_str)

            # EstimateItemに変換
            estimate_items = []
            for item_data in items_data:
                estimate_item = EstimateItem(
                    item_no=item_data.get("item_no", ""),
                    name=item_data.get("name", ""),
                    specification=item_data.get("specification"),
                    quantity=item_data.get("quantity"),
                    unit=item_data.get("unit", ""),
                    level=item_data.get("level", 2),
                    discipline=discipline,
                    parent_item=item_data.get("parent_item"),
                    confidence=item_data.get("confidence", 0.8),
                    estimation_basis=item_data.get("estimation_basis", "仕様書記載")
                )
                estimate_items.append(estimate_item)

            return estimate_items

        except Exception as e:
            logger.error(f"Error generating {discipline_name} items: {e}")
            return []

    def generate_estimate_unified(
        self,
        spec_pdf_path: str,
        legal_standards: list = None
    ) -> FMTDocument:
        """
        仕様書からAIで統合見積を自動生成（全工事区分を一括処理）

        電気・機械・ガスの区分を設けず、仕様書に記載された全ての設備項目を
        一括で抽出し、KBの全カテゴリから単価をマッチングする。

        Args:
            spec_pdf_path: 仕様書PDFのパス
            legal_standards: 適用法令リスト

        Returns:
            生成されたFMTDocument（全設備項目を含む）
        """
        if legal_standards is None:
            legal_standards = []
        logger.info("Starting unified estimate generation (all disciplines)")

        # 1. 仕様書からテキスト抽出
        spec_text = self.extract_text_from_pdf(spec_pdf_path)

        # 2. 建物情報を詳細抽出
        building_info = self.extract_building_info(spec_text)

        # 仕様書テキストを全て追加（制限なし）
        building_info["spec_text_excerpt"] = spec_text

        # 法令情報を追加
        if legal_standards:
            building_info["legal_standards"] = legal_standards

        # 2.5. 諸元表から詳細な部屋・設備情報を抽出
        spec_table_data = self.extract_specification_tables(spec_pdf_path, spec_text)
        if spec_table_data.get("rooms"):
            building_info["spec_table"] = spec_table_data
            equipment_summary = spec_table_data.get("equipment_summary", {})
            if equipment_summary.get("total_gas_outlets"):
                building_info.setdefault("facility_requirements", {}).setdefault("gas", {})["num_connection_points"] = equipment_summary["total_gas_outlets"]
            if equipment_summary.get("total_rooms"):
                building_info.setdefault("building_info", {})["num_rooms"] = equipment_summary["total_rooms"]

        # 2.6. Vision抽出による諸元表データ取得
        if HAS_PYMUPDF:
            vision_table_data = self.extract_specification_table_with_vision(spec_pdf_path)
            if vision_table_data.get("rooms"):
                building_info["spec_table_vision"] = vision_table_data
                totals = vision_table_data.get("totals", {})
                if totals.get("room_count"):
                    building_info.setdefault("building_info", {})["num_rooms"] = totals["room_count"]
                if totals.get("gas_outlet_total"):
                    building_info.setdefault("facility_requirements", {}).setdefault("gas", {})["num_connection_points"] = totals["gas_outlet_total"]
                if totals.get("electrical_outlet_total"):
                    building_info.setdefault("facility_requirements", {}).setdefault("electrical", {})["outlet_count"] = totals["electrical_outlet_total"]

        # 2.7. 図面から設備情報を抽出
        if HAS_PYMUPDF:
            drawing_info = self.extract_drawing_info(spec_pdf_path)
            if drawing_info.get("equipment_locations") or drawing_info.get("pipe_routes"):
                building_info["drawing_info"] = drawing_info

        # 3. 各工事区分を順番に生成してマージ（分割LLM呼び出し）
        logger.info("Generating unified estimate items using split LLM calls for all 6 categories")
        estimate_items = []

        # 3.1 電気設備工事
        logger.info("Generating electrical items...")
        electrical_items = self.generate_detailed_items_for_electrical(building_info)
        logger.info(f"Generated {len(electrical_items)} electrical items")
        estimate_items.extend(electrical_items)

        # 3.2 機械設備工事
        logger.info("Generating mechanical items...")
        mechanical_items = self.generate_detailed_items_for_mechanical(building_info)
        logger.info(f"Generated {len(mechanical_items)} mechanical items")
        estimate_items.extend(mechanical_items)

        # 3.3 ガス設備工事
        logger.info("Generating gas items...")
        gas_items = self.generate_detailed_items_for_gas(building_info)
        logger.info(f"Generated {len(gas_items)} gas items")
        estimate_items.extend(gas_items)

        # 3.4 空調設備工事
        logger.info("Generating HVAC items...")
        hvac_items = self.generate_detailed_items_generic(building_info, DisciplineType.HVAC)
        logger.info(f"Generated {len(hvac_items)} HVAC items")
        estimate_items.extend(hvac_items)

        # 3.5 衛生設備工事
        logger.info("Generating plumbing items...")
        plumbing_items = self.generate_detailed_items_generic(building_info, DisciplineType.PLUMBING)
        logger.info(f"Generated {len(plumbing_items)} plumbing items")
        estimate_items.extend(plumbing_items)

        # 3.6 消防設備工事
        logger.info("Generating fire protection items...")
        fire_items = self.generate_detailed_items_generic(building_info, DisciplineType.FIRE_PROTECTION)
        logger.info(f"Generated {len(fire_items)} fire protection items")
        estimate_items.extend(fire_items)

        logger.info(f"Generated total {len(estimate_items)} unified items across all 6 categories")

        # 3.7. チェックリストで項目網羅性を検証・数量推定
        checker = EstimationChecker()
        floor_area = building_info.get("building_info", {}).get("total_floor_area", 0) or 0
        num_rooms = building_info.get("building_info", {}).get("num_rooms", 0) or 0

        # 各工事区分のカバー率を検証
        coverage_results = {}
        for disc in [DisciplineType.ELECTRICAL, DisciplineType.MECHANICAL, DisciplineType.GAS]:
            disc_items = [item for item in estimate_items if item.discipline == disc]
            coverage = checker.check_item_coverage(disc_items, disc)
            coverage_results[disc.value] = coverage
            logger.info(f"Checklist coverage for {disc.value}: {coverage['coverage_rate']*100:.1f}%")

            # 不足項目を追加（カバー率が70%未満の場合）
            if coverage['coverage_rate'] < 0.7 and coverage['missing_items']:
                missing_items = checker.generate_missing_items(
                    disc_items, disc, floor_area, num_rooms
                )
                if missing_items:
                    estimate_items.extend(missing_items)
                    logger.info(f"Added {len(missing_items)} missing items for {disc.value}")

        # 数量推定を適用
        for disc in [DisciplineType.ELECTRICAL, DisciplineType.MECHANICAL, DisciplineType.GAS]:
            disc_items = [item for item in estimate_items if item.discipline == disc]
            checker.estimate_quantities(disc_items, disc, floor_area, num_rooms)

        # 4. KBから単価を取得（全カテゴリ使用）
        estimate_items = self.enrich_with_prices_unified(estimate_items)

        # 5. FMTDocumentを作成
        contract_period = building_info.get("contract_period", "")
        if isinstance(contract_period, dict):
            if "construction_period" in contract_period:
                contract_period = contract_period["construction_period"]
            elif "rental_period" in contract_period:
                contract_period = contract_period["rental_period"]
            else:
                contract_period = str(contract_period)

        project_info = ProjectInfo(
            project_name=building_info.get("project_name", ""),
            client_name=building_info.get("client_name", ""),
            location=building_info.get("location", ""),
            contract_period=contract_period,
            payment_terms="本紙記載内容のみ有効とする。",
            floor_area_m2=building_info.get("building_info", {}).get("total_floor_area"),
            num_rooms=building_info.get("building_info", {}).get("num_rooms")
        )

        # 全工事区分を含む（6カテゴリ）
        all_disciplines = [
            DisciplineType.ELECTRICAL,
            DisciplineType.MECHANICAL,
            DisciplineType.GAS,
            DisciplineType.HVAC,
            DisciplineType.PLUMBING,
            DisciplineType.FIRE_PROTECTION
        ]

        # 妥当性検証（主要3区分）
        unit_price_checks = {}
        for disc in [DisciplineType.ELECTRICAL, DisciplineType.MECHANICAL, DisciplineType.GAS]:
            disc_items = [item for item in estimate_items if item.discipline == disc]
            check = checker.validate_unit_price(disc_items, disc, "学校", floor_area)
            unit_price_checks[disc.value] = check
            logger.info(f"Unit price validation for {disc.value}: {check['message']}")

        # 6. ㎡単価補正（下限を下回る場合に調整項目を追加）
        correction_results = {}
        for disc in [DisciplineType.ELECTRICAL, DisciplineType.MECHANICAL, DisciplineType.GAS]:
            disc_items = [item for item in estimate_items if item.discipline == disc]
            correction = checker.apply_all_corrections(
                disc_items, disc, "学校", floor_area, auto_correct=True
            )
            correction_results[disc.value] = correction

            # 補正項目があれば追加
            if correction.get("items_added"):
                for correction_item in correction["items_added"]:
                    estimate_items.append(correction_item)
                    logger.info(
                        f"Added correction item for {disc.value}: "
                        f"¥{correction['correction_total']:,.0f}"
                    )

        fmt_doc = FMTDocument(
            created_at=datetime.now().isoformat(),
            project_info=project_info,
            facility_type=FacilityType.SCHOOL,
            disciplines=all_disciplines,
            estimate_items=estimate_items,
            metadata={
                "payment_terms": "本紙記載内容のみ有効とする。",
                "remarks": "法定福利費を含む。",
                "source": "AI統合自動生成",
                "building_info": building_info.get("building_info", {}),
                "checklist_coverage": coverage_results,
                "unit_price_checks": unit_price_checks,
                "correction_results": {
                    k: {
                        "original_amount": v.get("original_amount", 0),
                        "corrected_amount": v.get("corrected_amount", 0),
                        "correction_total": v.get("correction_total", 0),
                        "corrected": v.get("correction_total", 0) > 0
                    } for k, v in correction_results.items()
                },
            }
        )

        logger.info(f"Generated unified FMTDocument with {len(estimate_items)} items")
        return fmt_doc

    def _generate_unified_items(self, building_info: Dict[str, Any]) -> List[EstimateItem]:
        """
        仕様書から全設備項目を一括抽出

        電気・機械・ガス等の区分を設けず、仕様書に記載された全ての
        設備工事項目を網羅的に抽出する。
        """
        spec_text = building_info.get("spec_text_excerpt", "")

        prompt = f"""あなたは熟練の建築設備積算技術者です。以下の仕様書から全ての設備工事項目を抽出してください。

【重要な制約】
1. **仕様書に明記されている項目**を中心に抽出してください
2. 数量が明記されていない場合は、建物規模（面積、部屋数等）から合理的に推定してください
3. 数量の推定根拠を estimation_basis フィールドに記載してください
4. 推定数量は控えめに設定してください（過大見積を避ける）
5. 一式工事は quantity=1, unit="式" としてください
6. 電気・機械・ガス等の工事区分は気にせず、全ての設備項目を抽出してください

【抽出対象の設備工事】
- 電気設備工事（照明、コンセント、分電盤、幹線、接地等）
- 機械設備工事（空調、換気、給排水、衛生等）
- ガス設備工事（配管、ガス栓、器具等）
- その他の設備工事（防災、通信、昇降機等）

【建物情報】
- 工事名: {building_info.get('project_name', '不明')}
- 所在地: {building_info.get('location', '不明')}
- 延床面積: {building_info.get('building_info', {}).get('total_floor_area', '不明')}㎡
- 階数: {building_info.get('building_info', {}).get('floors', '不明')}
- 部屋数: {building_info.get('building_info', {}).get('num_rooms', '不明')}

【仕様書（全文）】
{spec_text}

【出力形式】
以下のJSON配列形式で出力してください：
```json
[
  {{
    "item_no": "1",
    "name": "項目名",
    "specification": "仕様（サイズ、規格等）",
    "quantity": 数量または null,
    "unit": "単位",
    "level": 0-2,
    "discipline": "電気設備工事/機械設備工事/ガス設備工事/その他",
    "parent_item": "親項目名（level>0の場合）または null",
    "confidence": 0.0-1.0,
    "estimation_basis": "仕様書記載/図面参照/一般標準"
  }}
]
```

【levelの定義】
- level 0: 大項目（例：電気設備工事、機械設備工事）
- level 1: 中項目（例：照明設備、配管工事）
- level 2: 小項目（例：LED照明器具、白ガス管15A）

仕様書を注意深く読み、記載されている全ての設備項目を漏れなく抽出してください。"""

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=16000,  # 大量の項目に対応
                temperature=0,  # 決定的に（毎回同じ結果）
                messages=[{"role": "user", "content": prompt}]
            )

            record_cost(
                operation="統合見積項目生成",
                model_name=self.model_name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                metadata={"source": "generate_unified_items"}
            )

            response_text = response.content[0].text
            logger.debug(f"Unified generation response length: {len(response_text)}")

            # JSON抽出
            json_start = response_text.find('[')
            json_end = response_text.rfind(']') + 1

            if json_start == -1 or json_end <= json_start:
                logger.error("No JSON array found in unified generation response")
                return []

            json_str = response_text[json_start:json_end]

            try:
                items_data = json.loads(json_str)
            except json.JSONDecodeError:
                json_str = repair_json_array(json_str)
                items_data = json.loads(json_str)

            # EstimateItemに変換
            estimate_items = []
            for item_data in items_data:
                # discipline文字列をDisciplineTypeに変換
                discipline_str = item_data.get("discipline", "その他")
                if "電気" in discipline_str:
                    discipline = DisciplineType.ELECTRICAL
                elif "機械" in discipline_str or "空調" in discipline_str or "給排水" in discipline_str:
                    discipline = DisciplineType.MECHANICAL
                elif "ガス" in discipline_str:
                    discipline = DisciplineType.GAS
                else:
                    discipline = DisciplineType.ELECTRICAL  # デフォルト

                estimate_item = EstimateItem(
                    item_no=item_data.get("item_no", ""),
                    name=item_data.get("name", ""),
                    specification=item_data.get("specification"),
                    quantity=item_data.get("quantity"),
                    unit=item_data.get("unit", ""),
                    level=item_data.get("level", 2),
                    discipline=discipline,
                    parent_item=item_data.get("parent_item"),
                    confidence=item_data.get("confidence", 0.8),
                    estimation_basis=item_data.get("estimation_basis", "仕様書記載")
                )
                estimate_items.append(estimate_item)

            logger.info(f"Parsed {len(estimate_items)} unified estimate items")
            return estimate_items

        except Exception as e:
            logger.error(f"Error in unified item generation: {e}")
            return []

    def enrich_with_prices_unified(self, estimate_items: List[EstimateItem]) -> List[EstimateItem]:
        """
        KBから単価を取得（全カテゴリ使用、discipline制限なし）

        全てのKB項目を検索対象とし、工事区分による絞り込みを行わない。
        """
        vector_search_available = self.vector_search and self.vector_search.is_available()
        logger.info(f"Enriching {len(estimate_items)} items with prices (unified, no discipline filter)")
        logger.info(f"KB items: {len(self.price_kb)}, vector_search={vector_search_available}")

        enriched_items = []
        match_count = 0

        for item in estimate_items:
            # 親項目（level 0）のみスキップ - 数量nullでも単価マッチングは試行
            if item.level == 0:
                enriched_items.append(item)
                continue

            # テキストを正規化
            item_name_norm = self._normalize_text(item.name)
            item_spec_norm = self._normalize_text(item.specification or "")
            item_size = self._extract_size(item.specification or "")
            item_category = self._get_category(item.name)

            # ===== ベクトル検索を試行（discipline制限なし） =====
            matched_item = None
            match_type = ""
            best_score = 0.0

            if vector_search_available:
                # discipline=Noneで全カテゴリ検索
                vector_result = self._vector_search_match(
                    item.name,
                    item.specification or "",
                    None  # discipline制限なし
                )
                if vector_result:
                    kb_item = vector_result["kb_item"]
                    if self._check_unit_compatibility(item.unit, kb_item.get("unit", "")):
                        if self._validate_price(item.name, kb_item.get("unit_price")):
                            matched_item = kb_item
                            match_type = "vector"
                            best_score = vector_result["score"]
                            match_count += 1

            # ===== フォールバック: 文字列マッチング（全KB検索） =====
            if not matched_item:
                best_match = None
                best_match_score = 0.0

                for kb_item in self.price_kb:
                    # discipline制限なし - 全KB項目を検索

                    kb_desc = kb_item.get("description", "")
                    kb_spec = kb_item.get("features", {}).get("specification", "")
                    kb_full_text = f"{kb_desc} {kb_spec}"

                    kb_desc_norm = self._normalize_text(kb_desc)
                    kb_spec_norm = self._normalize_text(kb_spec)
                    kb_full_norm = self._normalize_text(kb_full_text)
                    kb_size = self._extract_size(kb_spec)
                    kb_category = self._get_category(kb_desc)

                    # 類似度計算
                    score = 0.0

                    # 1. 項目名の一致
                    if item_name_norm == kb_desc_norm:
                        score += 2.0
                    elif item_name_norm in kb_desc_norm or kb_desc_norm in item_name_norm:
                        score += 1.5
                    elif any(word in kb_desc_norm for word in item_name_norm.split() if len(word) > 1):
                        score += 1.0

                    # 2. カテゴリの一致
                    if item_category and kb_category and item_category == kb_category:
                        score += 1.0

                    # 3. 仕様・サイズの一致
                    if item_spec_norm and kb_spec_norm:
                        if item_spec_norm == kb_spec_norm:
                            score += 1.5
                        elif item_size and kb_size and item_size == kb_size:
                            score += 1.2
                        elif item_spec_norm in kb_full_norm or kb_spec_norm in item_spec_norm:
                            score += 0.8

                    # 4. 単位互換性チェック
                    if not self._check_unit_compatibility(item.unit, kb_item.get("unit", "")):
                        continue

                    if score >= 2.0 and score > best_match_score:
                        best_match = kb_item
                        best_match_score = score

                if best_match:
                    matched_item = best_match
                    match_type = "string"
                    best_score = best_match_score
                    match_count += 1

            # 単価を設定（妥当性チェック付き）
            if matched_item:
                candidate_price = matched_item.get("unit_price")
                # 金額妥当性チェック
                if self._check_price_sanity(item.name, item.unit, candidate_price, item.quantity or 0):
                    item.unit_price = candidate_price
                    if item.quantity and item.unit_price:
                        item.amount = item.quantity * item.unit_price
                    item.source_reference = f"KB:{matched_item.get('item_id')}[{match_type}](score={best_score:.2f})"
                    item.price_references = [matched_item.get("item_id")]
                    logger.debug(f"✓ Matched '{item.name}' → {matched_item.get('item_id')} @¥{item.unit_price:,.0f}")
                else:
                    # 妥当性チェック失敗 - 単価を適用しない
                    logger.warning(f"✗ Price rejected for '{item.name}': ¥{candidate_price:,.0f} × {item.quantity} = ¥{candidate_price * (item.quantity or 0):,.0f}")
                    match_count -= 1  # マッチカウントを減らす

            enriched_items.append(item)

        match_rate = match_count / len([i for i in estimate_items if i.level > 0 and i.quantity]) * 100 if estimate_items else 0
        logger.info(f"Unified price matching: {match_count} items matched ({match_rate:.1f}%)")

        # 親項目の金額を子項目の合計で計算
        enriched_items = self._calculate_parent_amounts(enriched_items)

        # 階層的なナンバリングを割り当て
        enriched_items = self._assign_item_numbers(enriched_items)

        # 合計金額をログ出力
        total_amount = sum(item.amount or 0 for item in enriched_items if item.level == 0)
        logger.info(f"Total amount after parent calculation: ¥{total_amount:,.0f}")

        return enriched_items


if __name__ == "__main__":
    # テスト実行
    import sys
    sys.path.insert(0, '.')

    generator = AIEstimateGenerator()

    spec_path = "test-files/仕様書【都立山崎高等学校仮設校舎等の借入れ】ord202403101060100130187c1e4d0.pdf"

    if Path(spec_path).exists():
        print("\n" + "="*80)
        print("AI自動見積生成テスト")
        print("="*80)

        # 見積書を生成
        fmt_doc = generator.generate_estimate(
            spec_path,
            DisciplineType.GAS
        )

        print(f"\n【生成結果】")
        print(f"  工事名: {fmt_doc.project_info.project_name}")
        print(f"  項目数: {len(fmt_doc.estimate_items)}")

        # 合計金額を計算
        total = sum(item.amount or 0 for item in fmt_doc.estimate_items if item.level == 0)
        print(f"  合計金額: ¥{total:,.0f}")

        # 単価マッチング率
        with_price = sum(1 for item in fmt_doc.estimate_items if item.unit_price is not None)
        print(f"  単価マッチング率: {with_price}/{len(fmt_doc.estimate_items)} ({with_price/len(fmt_doc.estimate_items)*100:.1f}%)")

        # 階層別統計
        level_counts = {}
        for item in fmt_doc.estimate_items:
            level_counts[item.level] = level_counts.get(item.level, 0) + 1

        print(f"\n【階層別項目数】")
        for level in sorted(level_counts.keys()):
            print(f"  Level {level}: {level_counts[level]}項目")

        # 最初の30項目を表示
        print(f"\n【見積項目（最初の30項目）】")
        for i, item in enumerate(fmt_doc.estimate_items[:30]):
            indent = "  " * item.level
            spec_str = f" {item.specification}" if item.specification else ""
            qty_str = f" {item.quantity}{item.unit}" if item.quantity else ""
            price_str = f" @¥{item.unit_price:,.0f}" if item.unit_price else ""
            amount_str = f" = ¥{item.amount:,.0f}" if item.amount else ""
            conf = f" [信頼度:{item.confidence:.2f}]" if item.confidence else ""
            print(f"{indent}{item.name}{spec_str}{qty_str}{price_str}{amount_str}{conf}")

    else:
        print(f"❌ ファイルが見つかりません: {spec_path}")
