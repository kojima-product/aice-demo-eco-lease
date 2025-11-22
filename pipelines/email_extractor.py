"""
メール本文PDFから見積依頼情報を抽出するモジュール

見積依頼メールから以下を抽出:
- 工事名
- 工期
- レンタル期間
- 建屋面積
- 顧客名・担当者
- 見積期限
- その他補足情報
"""

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional, Dict, Any

from PyPDF2 import PdfReader
from pydantic import BaseModel, Field
import anthropic
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class EmailInfo(BaseModel):
    """メール本文から抽出された見積依頼情報"""

    # 基本情報
    project_name: Optional[str] = Field(None, description="工事名")
    client_company: Optional[str] = Field(None, description="依頼元会社名")
    client_contact: Optional[str] = Field(None, description="担当者名")
    client_email: Optional[str] = Field(None, description="担当者メールアドレス")
    client_phone: Optional[str] = Field(None, description="担当者電話番号")

    # 工期・期間
    construction_start: Optional[str] = Field(None, description="工期開始日")
    construction_end: Optional[str] = Field(None, description="工期終了日")
    rental_start: Optional[str] = Field(None, description="レンタル期間開始日")
    rental_end: Optional[str] = Field(None, description="レンタル期間終了日")
    rental_months: Optional[int] = Field(None, description="レンタル期間（月数）")

    # 建物情報
    building_area_tsubo: Optional[float] = Field(None, description="建屋面積（坪）")
    building_area_m2: Optional[float] = Field(None, description="建屋面積（㎡）")
    building_description: Optional[str] = Field(None, description="建屋構成の説明")

    # 見積条件
    quote_deadline: Optional[str] = Field(None, description="見積提出期限")
    required_disciplines: list[str] = Field(default_factory=list, description="必要な工事区分リスト")

    # その他
    remarks: Optional[str] = Field(None, description="その他特記事項")


class EmailExtractor:
    """メール本文PDFから見積依頼情報を抽出"""

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Anthropic API key（Noneの場合は環境変数から取得）
        """
        load_dotenv()
        self.client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """PDFからテキストを抽出"""
        logger.info(f"Extracting text from email PDF: {pdf_path}")

        reader = PdfReader(pdf_path)
        text_parts = []

        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)

        full_text = "\n\n".join(text_parts)
        logger.info(f"Extracted {len(full_text)} characters from {len(reader.pages)} pages")

        return full_text

    def extract_email_info(self, pdf_path: str) -> EmailInfo:
        """
        メール本文PDFから見積依頼情報を抽出

        Args:
            pdf_path: メール本文PDFのパス

        Returns:
            EmailInfo: 抽出された情報
        """
        # PDFからテキスト抽出
        text = self.extract_text_from_pdf(pdf_path)

        # Claude APIで構造化データを抽出
        logger.info("Extracting structured data from email using Claude API...")

        prompt = f"""以下は見積依頼のメール本文です。このメールから見積書作成に必要な情報を抽出してください。

【メール本文】
{text}

【抽出する情報】
以下のJSON形式で情報を抽出してください。情報が記載されていない項目はnullにしてください。

{{
  "project_name": "工事名",
  "client_company": "依頼元会社名",
  "client_contact": "担当者名",
  "client_email": "メールアドレス",
  "client_phone": "電話番号",
  "construction_start": "工期開始日（YYYY/MM/DD形式）",
  "construction_end": "工期終了日（YYYY/MM/DD形式）",
  "rental_start": "レンタル期間開始日（YYYY/MM/DD形式）",
  "rental_end": "レンタル期間終了日（YYYY/MM/DD形式）",
  "rental_months": レンタル期間の月数（整数）,
  "building_area_tsubo": 建屋面積の坪数（数値）,
  "building_area_m2": 建屋面積の平米数（数値、3.3058で計算）,
  "building_description": "建屋構成の詳細説明",
  "quote_deadline": "見積提出期限（YYYY/MM/DD形式）",
  "required_disciplines": ["必要な工事区分のリスト"],
  "remarks": "その他特記事項"
}}

【注意事項】
- 日付は元の形式（例: 2025/8）を維持してください
- 建屋面積が坪で記載されている場合、㎡換算も計算してください（1坪=3.3058㎡）
- 工事区分は以下から該当するものを選んでください: 電気設備, 弱電設備, 消防用設備, 給排水設備, ガス設備, 空調換気設備
- remarksには、見積作成時に参考になる補足情報を記載してください

JSON形式のみを出力してください。説明文は不要です。"""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            temperature=0,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )

        # レスポンスからJSONを抽出
        content = response.content[0].text
        logger.debug(f"Claude response: {content}")

        # JSONをパース
        try:
            # コードブロックがある場合は除去
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            data = json.loads(content)
            email_info = EmailInfo(**data)

            logger.info(f"Successfully extracted email info: {email_info.project_name}")
            logger.info(f"  Client: {email_info.client_company} - {email_info.client_contact}")
            logger.info(f"  Construction: {email_info.construction_start} ~ {email_info.construction_end}")
            logger.info(f"  Rental: {email_info.rental_start} ~ {email_info.rental_end} ({email_info.rental_months}ヶ月)")
            logger.info(f"  Building: {email_info.building_area_tsubo}坪 ({email_info.building_area_m2}㎡)")
            logger.info(f"  Deadline: {email_info.quote_deadline}")
            logger.info(f"  Disciplines: {', '.join(email_info.required_disciplines)}")

            return email_info

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.error(f"Response content: {content}")
            # 空のEmailInfoを返す
            return EmailInfo()
        except Exception as e:
            logger.error(f"Error creating EmailInfo: {e}")
            return EmailInfo()


def test_email_extraction():
    """メール本文抽出のテスト"""

    email_pdf = "test-files/メール本文.pdf"

    if not Path(email_pdf).exists():
        print(f"❌ テストファイルが見つかりません: {email_pdf}")
        return

    print("=" * 80)
    print("メール本文情報抽出テスト")
    print("=" * 80)

    extractor = EmailExtractor()
    email_info = extractor.extract_email_info(email_pdf)

    print("\n【抽出結果】")
    print(f"工事名: {email_info.project_name}")
    print(f"依頼元: {email_info.client_company}")
    print(f"担当者: {email_info.client_contact} ({email_info.client_email})")
    print(f"電話: {email_info.client_phone}")
    print(f"\n工期: {email_info.construction_start} ～ {email_info.construction_end}")
    print(f"レンタル期間: {email_info.rental_start} ～ {email_info.rental_end} ({email_info.rental_months}ヶ月)")
    print(f"\n建屋面積: {email_info.building_area_tsubo}坪 ({email_info.building_area_m2}㎡)")
    print(f"建屋構成: {email_info.building_description}")
    print(f"\n見積期限: {email_info.quote_deadline}")
    print(f"必要工事区分: {', '.join(email_info.required_disciplines)}")
    print(f"\nその他: {email_info.remarks}")

    print("\n✅ テスト完了")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_email_extraction()
