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

try:
    import fitz  # PyMuPDF for image extraction
    from PIL import Image
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    logger.warning("PyMuPDF not available - drawing extraction disabled")

from pipelines.schemas import (
    EstimateItem, DisciplineType, FMTDocument, ProjectInfo, FacilityType,
    CostType
)
from pipelines.cost_tracker import record_cost


# ===== Phase 2: 類義語辞書（KBマッチング精度向上用）=====
SYNONYM_DICT = {
    # 電気設備
    "気中開閉器": ["PAS", "高圧気中負荷開閉器", "高圧気中開閉器", "気中負荷開閉器"],
    "架橋ポリエチレンケーブル": ["CV", "CVT", "高圧ケーブル", "CVケーブル", "CVTケーブル"],
    "キュービクル": ["高圧受電設備", "受変電設備", "受電設備"],
    "ビニル絶縁電線": ["IV", "IV電線", "600V IV"],
    "接地工事": ["A種接地", "B種接地", "C種接地", "D種接地", "接地"],
    "分電盤": ["動力盤", "電灯盤", "配電盤"],
    "LED照明": ["LED", "LED器具", "照明器具"],
    "非常照明": ["非常用照明", "誘導灯"],
    "自動火災報知設備": ["自火報", "火災報知器", "火報"],
    # 機械設備
    "空冷ヒートポンプ": ["エアコン", "空調機", "ヒートポンプ", "EHP"],
    "換気扇": ["換気設備", "排気ファン", "給気ファン"],
    "給水ポンプ": ["加圧給水ポンプ", "揚水ポンプ", "ポンプユニット"],
    "受水槽": ["貯水槽", "FRP受水槽"],
    "給湯器": ["電気温水器", "ガス給湯器", "給湯設備"],
    "衛生器具": ["便器", "洗面器", "流し台", "手洗器"],
    # ガス設備
    "白ガス管": ["鋼管", "ガス管", "SGP"],
    "PE管": ["ポリエチレン管", "ポリ管"],
    "ガスコンセント": ["ガス栓", "コンセント"],
    "ネジコック": ["コック", "バルブ"],
}

# 高額機器リスト（単価妥当性チェック用）
HIGH_VALUE_ITEMS = {
    "キュービクル": 1000000,      # 最低100万円
    "高圧受電設備": 1000000,
    "受変電設備": 1000000,
    "変圧器": 500000,             # 最低50万円
    "高圧変圧器": 500000,
    "発電機": 2000000,            # 最低200万円
    "非常用発電機": 2000000,
    "エレベーター": 5000000,      # 最低500万円
    "昇降機": 5000000,
    "空調機": 100000,             # 最低10万円
    "エアコン": 50000,            # 最低5万円
    "給水ポンプ": 200000,         # 最低20万円
    "受水槽": 300000,             # 最低30万円
}


class AIEstimateGenerator:
    """
    AI自動見積生成器

    仕様書から建物情報を抽出し、建築設備の専門知識を使って
    詳細な見積項目（配管サイズ、数量、材料等）を自動生成します。
    """

    def __init__(self, kb_path: str = "kb/price_kb.json"):
        load_dotenv()
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self.kb_path = kb_path
        self.price_kb = self._load_price_kb()

    def _load_price_kb(self) -> List[Dict]:
        """価格KBを読み込み"""
        if os.path.exists(self.kb_path):
            with open(self.kb_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        logger.warning(f"Price KB not found: {self.kb_path}")
        return []

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

        for keyword, min_price in HIGH_VALUE_ITEMS.items():
            keyword_norm = self._normalize_text(keyword)
            if keyword_norm in item_name_norm:
                if matched_price < min_price:
                    logger.warning(
                        f"Price validation failed: '{item_name}' matched ¥{matched_price:,.0f} "
                        f"but minimum expected is ¥{min_price:,.0f}"
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

    def extract_text_from_pdf(self, pdf_path: str, max_pages: int = 50) -> str:
        """PDFからテキストを抽出（ページ番号マーカー付き）"""
        logger.info(f"Extracting text from PDF: {pdf_path}")

        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                total_pages = min(len(pdf_reader.pages), max_pages)
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

        response_text = response.content[0].text

        # JSONを抽出
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1

        if json_start == -1 or json_end == 0:
            logger.error("No JSON found in specification table response")
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

        response_text = response.content[0].text

        # JSONを抽出
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1

        if json_start == -1 or json_end == 0:
            logger.error("No JSON found in response")
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

        prompt = f"""あなたは建築設備（ガス設備）の設計専門家です。以下の建物情報から、都市ガス設備工事の詳細な見積項目を設計してください。

建物情報:
{building_summary}
{spec_table_info}
{drawing_info_text}

【設計タスク】
実際の設備設計と同様に、以下の項目を含む詳細な見積を作成してください：

1. **基本工事費**: 図面作成、申請業務、現場管理等
2. **配管工事費**:
   - 各サイズの配管（15A, 20A, 25A, 32A, 50A, 80A）
   - 延長メートル数を建物規模から推定
   - 材質（白ガス管、カラー鋼管、PE管等）を適切に選定
   - 露出結び（配管接続）
3. **ガス栓等材料費**:
   - ガスコンセント（S型露出、W型露出）
   - ネジコック
   - 各サイズ・個数を用途から推定（諸元表データがあれば活用）
4. **特別材料費**:
   - 分岐コック
   - ボールスライドジョイント
5. **付帯工事費**:
   - 配管撤去（既存設備がある場合）
   - 配管支持金具
   - 穴補修、埋戻し
   - コンクリート切断・復旧
   - 高所作業車
6. **機器搬続費**:
   - 資機材運搬費
   - 諸経費

【設計の考え方】
- 諸元表のデータがある場合は、それを基に正確な数量を算出
- 建物面積から配管総延長を推定（例: 2,145㎡ → 約400-500m）
- 用途（学校）から各部屋のガス栓数を推定
- 配管サイズの割合: 15A(20%), 20A(30%), 25A(20%), 32A(15%), 50A(10%), 80A(5%)
- 仮設建物なので解体費・撤去費を考慮

【出力形式】
JSON配列で、階層構造を持った見積項目を出力してください：

```json
[
  {{
    "item_no": "1",
    "level": 0,
    "name": "都市ガス設備工事",
    "specification": "",
    "quantity": null,
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
    "quantity": null,
    "unit": "",
    "unit_price": null,
    "amount": null,
    "cost_type": "材料費",
    "remarks": "",
    "confidence": 0.85
  }},
  {{
    "item_no": "",
    "level": 2,
    "name": "白ガス管（ネジ接合）",
    "specification": "15A",
    "quantity": 93,
    "unit": "m",
    "unit_price": null,
    "amount": null,
    "cost_type": "材料費",
    "remarks": "",
    "confidence": 0.8,
    "estimation_basis": "建物面積2,145㎡×4%≒86m、教室配置を考慮して93m"
  }},
  ... (可能な限り詳細に)
]
```

仕様書に記載されているすべての設備項目について、詳細な見積を生成してください。項目数に制限はありません。
単価はnullのままで構いません（後でKBから取得します）。"""

        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=16000,
            temperature=0.3,  # 少し創造性を持たせる
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text
        logger.debug(f"LLM Response: {response_text[:500]}...")

        # JSONを抽出
        json_start = response_text.find('[')
        json_end = response_text.rfind(']') + 1

        if json_start == -1 or json_end == 0:
            logger.error("No JSON found in response")
            return []

        json_str = response_text[json_start:json_end]
        items_data = json.loads(json_str)

        logger.info(f"Generated {len(items_data)} detailed items for gas equipment")

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
        電気設備の詳細見積項目をAI生成

        建物情報から、受変電設備・幹線・照明・コンセント等を設計レベルで推定します。
        """
        logger.info("Generating detailed electrical equipment items")

        # 建物情報を簡潔に抽出（プロンプトサイズ削減）
        bldg = building_info.get("building_info", {})
        elec = building_info.get("facility_requirements", {}).get("electrical", {})

        building_summary = f"""【建物概要】
- 工事名: {building_info.get('project_name', '仮設校舎')}
- 延床面積: {bldg.get('total_floor_area', 2000)}㎡
- 階数: {bldg.get('floors', 3)}階
- 部屋数: {bldg.get('num_rooms', 50)}室
- 構造: {bldg.get('structure', '軽量鉄骨造')}

【電気設備要件】
- 受電電圧: {elec.get('voltage', '高圧受電（6,600V）')}
- 受電容量: {elec.get('estimated_capacity_kva', 500)}kVA
- 詳細: {elec.get('details', '')}"""

        # 諸元表から部屋数サマリ
        spec_table_info = ""
        if "spec_table" in building_info:
            spec_table = building_info["spec_table"]
            summary = spec_table.get("equipment_summary", {})
            spec_table_info = f"""
【諸元表サマリ】
- 総部屋数: {summary.get('total_rooms', 'N/A')}室
- 総面積: {summary.get('total_area_m2', 'N/A')}㎡"""

        prompt = f"""電気設備工事の詳細見積項目を生成してください。仕様書に記載されているすべての設備項目について、漏れなく詳細な見積を生成してください。項目数に制限はありません。

{building_summary}
{spec_table_info}

以下のカテゴリを網羅し、各カテゴリで必要なすべての項目を生成：
1. 受変電設備（キュービクル、高圧ケーブル、変圧器、高圧機器、接地工事）
2. 非常用発電設備（発電機、燃料タンク、自動切替装置）
3. 幹線設備（幹線ケーブルCV各サイズ、電線管、ケーブルラック）
4. 分電盤設備（動力盤、電灯盤、非常用分電盤）
5. 照明設備（LED各種、教室用、廊下用、誘導灯、非常照明）
6. コンセント設備（一般、OA、床、専用、防水）
7. 弱電設備（LAN、電話、放送、インターホン、テレビ共聴）
8. 防災設備（自火報、非常放送、感知器、発信機）
9. 配線・配管工事（電線各サイズ、PF管、金属管）
10. 付帯工事（仮設電力、撤去、試験検査、諸経費）

【出力形式】
JSON配列で、階層構造を持った見積項目を出力してください。JSONのみを出力し、マークダウンの```は不要です：

[
  {{
    "item_no": "1",
    "level": 0,
    "name": "電気設備工事",
    "specification": "",
    "quantity": null,
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
    "name": "受変電設備",
    "specification": "",
    "quantity": null,
    "unit": "",
    "unit_price": null,
    "amount": null,
    "cost_type": "機器費",
    "remarks": "",
    "confidence": 0.9
  }},
  {{
    "item_no": "",
    "level": 2,
    "name": "高圧受電設備（キュービクル）",
    "specification": "屋外型 500kVA",
    "quantity": 1,
    "unit": "式",
    "unit_price": null,
    "amount": null,
    "cost_type": "機器費",
    "remarks": "消防認定品",
    "confidence": 0.85,
    "estimation_basis": "仕様書記載の受電容量から算定"
  }}
]

仕様書に記載されているすべての電気設備項目について、詳細な見積を生成してください。
単価はnullのままで構いません（後でKBから取得します）。"""

        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=16000,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text
        logger.debug(f"LLM Response for electrical: {response_text[:500]}...")

        # JSONを抽出（マークダウンコードブロックを除去）
        import re
        # ```json ... ``` を除去（改行を含む）
        response_text = re.sub(r'```json\s*\n?', '', response_text)
        response_text = re.sub(r'\n?```\s*$', '', response_text)
        response_text = re.sub(r'\n?```\s*\n?', '', response_text)

        json_start = response_text.find('[')
        json_end = response_text.rfind(']') + 1

        if json_start == -1 or json_end == 0:
            logger.error(f"No JSON found in electrical response. Response starts with: {response_text[:200]}")
            return []

        json_str = response_text[json_start:json_end]

        try:
            items_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}. JSON starts with: {json_str[:200]}")
            return []

        logger.info(f"Generated {len(items_data)} detailed items for electrical equipment")

        # EstimateItemに変換
        estimate_items = []
        for item_data in items_data:
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
                discipline=DisciplineType.ELECTRICAL,
                cost_type=cost_type,
                remarks=item_data.get("remarks", ""),
                source_type="ai_generated",
                source_reference=item_data.get("estimation_basis", "AI設計"),
                confidence=item_data.get("confidence", 0.7)
            )

            estimate_items.append(estimate_item)

        return estimate_items

    def generate_detailed_items_for_mechanical(
        self,
        building_info: Dict[str, Any]
    ) -> List[EstimateItem]:
        """
        機械設備の詳細見積項目をAI生成

        建物情報から、空調・給排水・換気・消火設備等を設計レベルで推定します。
        """
        logger.info("Generating detailed mechanical equipment items")

        # 建物情報を簡潔に抽出
        bldg = building_info.get("building_info", {})
        mech = building_info.get("facility_requirements", {}).get("mechanical", {})

        building_summary = f"""【建物概要】
- 工事名: {building_info.get('project_name', '仮設校舎')}
- 延床面積: {bldg.get('total_floor_area', 2000)}㎡
- 階数: {bldg.get('floors', 3)}階
- 部屋数: {bldg.get('num_rooms', 50)}室
- 構造: {bldg.get('structure', '軽量鉄骨造')}

【機械設備要件】
- 空調方式: {mech.get('hvac_type', '冷暖房設備')}
- 給排水: {mech.get('plumbing', True)}
- 詳細: {mech.get('details', '')}"""

        # 諸元表サマリ
        spec_table_info = ""
        if "spec_table" in building_info:
            spec_table = building_info["spec_table"]
            summary = spec_table.get("equipment_summary", {})
            spec_table_info = f"""
【諸元表サマリ】
- 総部屋数: {summary.get('total_rooms', 'N/A')}室
- 総面積: {summary.get('total_area_m2', 'N/A')}㎡"""

        prompt = f"""機械設備工事の詳細見積項目を生成してください。仕様書に記載されているすべての設備項目について、漏れなく詳細な見積を生成してください。項目数に制限はありません。

{building_summary}
{spec_table_info}

以下のカテゴリを網羅し、各カテゴリで必要なすべての項目を生成：
1. 空調設備（室外機各サイズ、室内機各タイプ、全熱交換機、冷媒配管、ドレン配管）
2. 換気設備（換気扇各種、ダクト、給排気口）
3. 給水設備（加圧給水ポンプ、受水槽、給水管各サイズ、止水弁、量水器）
4. 給湯設備（電気温水器、ガス給湯器、給湯管）
5. 排水設備（排水管各サイズ、排水桝、通気管、グリストラップ）
6. 消火設備（消火栓、消火ポンプ、消火水槽、配管）
7. 衛生器具（大便器、小便器、洗面器、流し台、手洗器）
8. 昇降機設備（乗用エレベーター、付帯設備）
9. 付帯工事（保温保冷、塗装、試験検査、諸経費）

【出力形式】
JSON配列で、階層構造を持った見積項目を出力してください。JSONのみを出力し、マークダウンの```は不要です：

[
  {{
    "item_no": "1",
    "level": 0,
    "name": "機械設備工事",
    "specification": "",
    "quantity": null,
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
    "name": "空調設備",
    "specification": "",
    "quantity": null,
    "unit": "",
    "unit_price": null,
    "amount": null,
    "cost_type": "機器費",
    "remarks": "",
    "confidence": 0.9
  }},
  {{
    "item_no": "",
    "level": 2,
    "name": "空冷ヒートポンプエアコン",
    "specification": "壁掛型 2.8kW",
    "quantity": 21,
    "unit": "台",
    "unit_price": null,
    "amount": null,
    "cost_type": "機器費",
    "remarks": "普通教室用",
    "confidence": 0.85,
    "estimation_basis": "普通教室21室×1台"
  }}
]

仕様書に記載されているすべての機械設備項目について、詳細な見積を生成してください。
単価はnullのままで構いません（後でKBから取得します）。"""

        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=16000,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text
        logger.debug(f"LLM Response for mechanical: {response_text[:500]}...")

        # JSONを抽出（マークダウンコードブロックを除去）
        import re
        response_text = re.sub(r'```json\s*\n?', '', response_text)
        response_text = re.sub(r'\n?```\s*$', '', response_text)
        response_text = re.sub(r'\n?```\s*\n?', '', response_text)

        json_start = response_text.find('[')
        json_end = response_text.rfind(']') + 1

        if json_start == -1 or json_end == 0:
            logger.error(f"No JSON found in mechanical response. Response starts with: {response_text[:200]}")
            return []

        json_str = response_text[json_start:json_end]

        try:
            items_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}. JSON starts with: {json_str[:200]}")
            return []

        logger.info(f"Generated {len(items_data)} detailed items for mechanical equipment")

        # EstimateItemに変換
        estimate_items = []
        for item_data in items_data:
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
                discipline=DisciplineType.MECHANICAL,
                cost_type=cost_type,
                remarks=item_data.get("remarks", ""),
                source_type="ai_generated",
                source_reference=item_data.get("estimation_basis", "AI設計"),
                confidence=item_data.get("confidence", 0.7)
            )

            estimate_items.append(estimate_item)

        return estimate_items

    def _normalize_text(self, text: str) -> str:
        """テキストを正規化（空白・記号を統一）"""
        if not text:
            return ""
        import re
        # 全角→半角
        text = text.replace('（', '(').replace('）', ')').replace('　', ' ')
        # 記号の統一
        text = text.replace('・', '').replace('/', '').replace('-', '')
        # 複数空白を1つに
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()

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
        KBから単価を取得して項目に付与（改善版）

        Args:
            estimate_items: 単価未設定の見積項目リスト

        Returns:
            単価・金額が設定された見積項目リスト
        """
        logger.info(f"Enriching {len(estimate_items)} items with prices from KB ({len(self.price_kb)} KB items loaded)")

        enriched_items = []

        for item in estimate_items:
            # 親項目（level 0や単価不要項目）はスキップ
            if item.level == 0 or not item.quantity:
                enriched_items.append(item)
                continue

            # テキストを正規化
            item_name_norm = self._normalize_text(item.name)
            item_spec_norm = self._normalize_text(item.specification or "")
            item_size = self._extract_size(item.specification or "")
            item_category = self._get_category(item.name)

            logger.debug(f"Matching: '{item.name}' {item.specification} | discipline={item.discipline.value}")
            logger.debug(f"  Normalized: name='{item_name_norm}', spec='{item_spec_norm}', size={item_size}, category={item_category}")

            # KBから類似項目を検索
            best_match = None
            best_score = 0.0
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

            matched_item = None
            match_type = ""

            if best_match and best_score >= 1.0:
                # 高品質マッチ（項目名+仕様が一致）
                matched_item = best_match
                match_type = "exact"
                logger.debug(f"✓ Exact match '{item.name}' → '{best_match.get('item_id')}' (score={best_score:.2f})")
            elif best_match and best_score >= 0.5:
                # 中品質マッチ（項目名 or カテゴリが一致）
                matched_item = best_match
                match_type = "partial"
                logger.debug(f"≈ Partial match '{item.name}' → '{best_match.get('item_id')}' (score={best_score:.2f})")
            elif category_fallback and category_fallback_score >= 0.8:
                # カテゴリフォールバック（カテゴリは一致するが仕様が異なる）
                matched_item = category_fallback
                match_type = "category"
                logger.debug(f"↳ Category fallback '{item.name}' → '{category_fallback.get('item_id')}' (score={category_fallback_score:.2f})")
            else:
                logger.warning(f"✗ No match for '{item.name}' {item.specification} (best={best_score:.2f})")

            if matched_item:
                # 最大スコア5.0として正規化（50%=2.5）
                normalized_score = min(best_score / 5.0, 1.0)
                confidence_pct = int(normalized_score * 100)

                # Phase 2: 単価妥当性チェック
                matched_price = matched_item.get("unit_price")
                price_valid = self._validate_price(item.name, matched_price)

                # 50%以上のマッチングで単価を適用（閾値緩和）
                if (normalized_score >= 0.50 or best_score >= 1.0) and price_valid:
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

        matched_count = sum(1 for item in enriched_items if item.unit_price is not None)
        if len(estimate_items) > 0:
            logger.info(f"Price matching: {matched_count}/{len(estimate_items)} items ({matched_count/len(estimate_items)*100:.1f}%)")
        else:
            logger.warning("No items to match prices for")

        return enriched_items

    def _calculate_parent_amounts(self, items: List[EstimateItem]) -> List[EstimateItem]:
        """親項目の金額を子項目の合計で計算（逆順で処理：Level高→低）"""
        # 逆順で処理（子項目から親項目へ）
        for i in range(len(items) - 1, -1, -1):
            item = items[i]

            # 子項目の有無をチェック
            has_children = False
            for j in range(i+1, len(items)):
                if items[j].level <= item.level:
                    break
                if items[j].level == item.level + 1:
                    has_children = True
                    break

            # 子項目がある場合は必ず子項目の合計で上書き
            if has_children:
                total = 0
                for j in range(i+1, len(items)):
                    if items[j].level <= item.level:
                        break
                    if items[j].level == item.level + 1:
                        total += items[j].amount or 0
                item.amount = total if total > 0 else None
                # 親項目の単価はクリア（子項目の合計のみを使用）
                item.unit_price = None

        return items

    def generate_estimate(
        self,
        spec_pdf_path: str,
        discipline: DisciplineType
    ) -> FMTDocument:
        """
        仕様書からAIで詳細見積を自動生成

        Args:
            spec_pdf_path: 仕様書PDFのパス
            discipline: 工事区分

        Returns:
            生成されたFMTDocument
        """
        logger.info(f"Starting AI-based estimate generation for {discipline.value}")

        # 1. 仕様書からテキスト抽出
        spec_text = self.extract_text_from_pdf(spec_pdf_path)

        # 2. 建物情報を詳細抽出
        building_info = self.extract_building_info(spec_text)

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
        if discipline == DisciplineType.GAS:
            estimate_items = self.generate_detailed_items_for_gas(building_info)
        elif discipline == DisciplineType.ELECTRICAL:
            logger.info(f"電気設備のAI自動生成")
            estimate_items = self.generate_detailed_items_for_electrical(building_info)
        elif discipline == DisciplineType.MECHANICAL:
            logger.info(f"機械設備のAI自動生成")
            estimate_items = self.generate_detailed_items_for_mechanical(building_info)
        else:
            logger.warning(f"{discipline.value} is not yet implemented")
            estimate_items = []

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
            floor_area_m2=building_info.get("building_info", {}).get("total_floor_area"),
            num_rooms=building_info.get("building_info", {}).get("num_rooms")
        )

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
                "building_info": building_info.get("building_info", {})
            }
        )

        logger.info(f"Generated FMTDocument with {len(estimate_items)} items")
        return fmt_doc


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
