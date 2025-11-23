"""
OCR処理を使って画像ベースPDFから見積データを抽出
"""
import os
import base64
import io
from typing import List, Dict, Any
from pathlib import Path
import fitz  # PyMuPDF
from PIL import Image
from anthropic import Anthropic
from loguru import logger
from dotenv import load_dotenv
from pipelines.cost_tracker import record_cost

# 環境変数をロード
load_dotenv()


class OCRExtractor:
    """画像ベースPDFからOCRで見積データを抽出"""

    def __init__(self):
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = "claude-sonnet-4-20250514"

    def pdf_to_images(self, pdf_path: str, dpi: int = 200) -> List[Image.Image]:
        """
        PDFを画像に変換

        Args:
            pdf_path: PDFファイルパス
            dpi: 解像度（デフォルト200）

        Returns:
            PIL Image のリスト
        """
        logger.info(f"Converting PDF to images: {pdf_path}")

        doc = fitz.open(pdf_path)
        images = []

        for page_num in range(len(doc)):
            page = doc[page_num]

            # ページを画像に変換（高解像度）
            mat = fitz.Matrix(dpi/72, dpi/72)  # 72 DPI がデフォルト
            pix = page.get_pixmap(matrix=mat)

            # PIL Imageに変換
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            images.append(img)

            logger.debug(f"Converted page {page_num+1}/{len(doc)}")

        doc.close()
        logger.info(f"Converted {len(images)} pages to images")
        return images

    def image_to_base64(self, image: Image.Image) -> str:
        """PIL ImageをBase64エンコード"""
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    def extract_estimate_from_images(
        self,
        images: List[Image.Image],
        discipline: str = "ガス設備工事"
    ) -> List[Dict[str, Any]]:
        """
        画像から見積項目を抽出（Claude Vision API使用）

        Args:
            images: 見積書の画像リスト
            discipline: 工事区分

        Returns:
            見積項目のリスト
        """
        logger.info(f"Extracting estimate items from {len(images)} images using Claude Vision API")

        all_items = []

        for i, image in enumerate(images, 1):
            logger.info(f"Processing image {i}/{len(images)}")

            # 画像をBase64エンコード
            image_base64 = self.image_to_base64(image)

            # Claude Vision APIで画像から見積項目を抽出
            prompt = f"""
この画像は「{discipline}」の見積書の一部です。

以下の情報を **すべて** 抽出してJSON配列で出力してください：

1. 項目名（例: 白ガス管、配管支持金具）
2. 仕様（例: 15A、20A、ネジ接合）
3. 数量（数値のみ）
4. 単位（例: m、個、式）
5. 単価（数値のみ、カンマなし）
6. 金額（数値のみ、カンマなし）

**重要な注意事項：**
- すべての明細行を漏れなく抽出してください
- 親項目・中項目・子項目の階層構造を保持してください
- 仕様欄が空白の場合は空文字""にしてください
- 数量・単価・金額が空白の場合はnullにしてください
- 項目名と仕様は必ず記載してください

JSON形式（配列）：
[
  {{
    "item_no": "1",
    "name": "項目名",
    "specification": "仕様",
    "quantity": 数量,
    "unit": "単位",
    "unit_price": 単価,
    "amount": 金額,
    "level": 階層レベル（0=親, 1=中, 2=子）
  }},
  ...
]

画像内のすべての項目を抽出してください。"""

            try:
                response = self.client.messages.create(
                    model=self.model_name,
                    max_tokens=16000,
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
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }]
                )

                # コスト記録
                record_cost(
                    operation="OCR見積抽出",
                    model_name=self.model_name,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    metadata={"source": "extract_estimate_from_images", "page": i, "discipline": discipline}
                )

                # レスポンスからJSONを抽出
                content = response.content[0].text

                # ```json ... ``` のマーカーを除去
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]

                # JSONパース
                import json
                items = json.loads(content.strip())

                logger.debug(f"Extracted {len(items)} items from page {i}")
                all_items.extend(items)

            except Exception as e:
                logger.error(f"Failed to extract from page {i}: {e}")
                continue

        logger.info(f"Total extracted items: {len(all_items)}")
        return all_items

    def extract_from_pdf(
        self,
        pdf_path: str,
        discipline: str = "ガス設備工事",
        dpi: int = 200
    ) -> List[Dict[str, Any]]:
        """
        PDFから見積項目を抽出（エンドツーエンド）

        Args:
            pdf_path: PDFファイルパス
            discipline: 工事区分
            dpi: 画像解像度

        Returns:
            見積項目のリスト
        """
        logger.info(f"Extracting estimate from PDF: {pdf_path}")

        # PDFを画像に変換
        images = self.pdf_to_images(pdf_path, dpi=dpi)

        # 画像から見積項目を抽出
        items = self.extract_estimate_from_images(images, discipline=discipline)

        return items

    def convert_to_kb_format(
        self,
        items: List[Dict[str, Any]],
        discipline: str = "ガス",
        source_file: str = ""
    ) -> List[Dict[str, Any]]:
        """
        抽出データをKBフォーマットに変換

        Args:
            items: 抽出された見積項目
            discipline: 工事区分（"ガス"、"電気"、"機械"等）
            source_file: 元ファイル名

        Returns:
            KBフォーマットの項目リスト
        """
        logger.info(f"Converting {len(items)} items to KB format")

        # IDプレフィックスマッピング
        id_prefix_map = {
            "ガス": "GAS",
            "電気": "ELEC",
            "機械": "MECH"
        }
        id_prefix = id_prefix_map.get(discipline, discipline.upper())

        kb_items = []
        item_counter = 1

        for item in items:
            # 親項目や金額のみの項目はスキップ
            if item.get('level', 2) == 0:
                continue

            # 単価が設定されている項目のみKB化
            if not item.get('unit_price'):
                continue

            kb_item = {
                "item_id": f"{id_prefix}_{item_counter:03d}",
                "description": item['name'],
                "discipline": f"{discipline}設備工事" if discipline != "ガス" else "ガス設備工事",
                "unit": item.get('unit', ''),
                "unit_price": float(item['unit_price']),
                "features": {
                    "specification": item.get('specification', ''),
                    "quantity": item.get('quantity'),
                    "source": source_file
                },
                "context_tags": []
            }

            kb_items.append(kb_item)
            item_counter += 1

        logger.info(f"Converted to {len(kb_items)} KB items")
        return kb_items


if __name__ == "__main__":
    # テスト用
    extractor = OCRExtractor()

    # 参照見積書PDFから抽出
    pdf_path = "test-files/250918_送付状　見積書（都市ｶﾞｽ).pdf"

    if Path(pdf_path).exists():
        items = extractor.extract_from_pdf(pdf_path, discipline="ガス設備工事", dpi=200)

        print(f"\n抽出された項目数: {len(items)}")
        print("\n=== 抽出結果（最初の10項目）===")
        for i, item in enumerate(items[:10], 1):
            print(f"{i}. {item.get('name')} {item.get('specification', '')} "
                  f"x{item.get('quantity', '')} {item.get('unit', '')} "
                  f"@¥{item.get('unit_price', 0):,}")

        # KBフォーマットに変換
        kb_items = extractor.convert_to_kb_format(items, discipline="ガス", source_file=pdf_path)

        print(f"\nKB変換後: {len(kb_items)}項目")
    else:
        print(f"File not found: {pdf_path}")
