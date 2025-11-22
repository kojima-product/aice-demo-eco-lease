"""Project Info Extractor - 仕様書から工事情報を抽出"""

import os
from typing import Dict, Any
from loguru import logger
from anthropic import Anthropic
from dotenv import load_dotenv

from pipelines.schemas import ProjectInfo


class ProjectInfoExtractor:
    """仕様書テキストから工事情報を抽出"""

    def __init__(self):
        """Claude LLMを初期化"""
        load_dotenv()
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
        logger.info(f"ProjectInfoExtractor initialized: {self.model_name}")

    def extract_project_info(self, raw_text: str, existing_info: ProjectInfo) -> ProjectInfo:
        """
        仕様書テキストから工事情報を抽出してProjectInfoを更新

        Args:
            raw_text: 仕様書の生テキスト
            existing_info: 既存のProjectInfo

        Returns:
            更新されたProjectInfo
        """
        logger.info("Extracting project information from specification text...")

        # Claude APIで情報抽出
        prompt = f"""以下の入札仕様書から、工事情報を抽出してください。

仕様書テキスト:
{raw_text[:60000]}

抽出する項目:
1. 工事名（project_name）: 正式な工事名称
2. 工事場所（location）: 所在地・住所
3. リース期間（contract_period）: 契約期間やリース期間（例: 25ヶ月（2026.8.1～2028.8.31）見積有効期間6ヶ月）
4. 決済条件（payment_terms）: 決済条件や支払い条件
5. 備考（remarks）: その他重要な備考（例: 法定福利費を含む）

以下のJSON形式で回答してください。情報が見つからない場合は null を返してください:
```json
{{
  "project_name": "工事名",
  "location": "所在地",
  "contract_period": "契約期間",
  "payment_terms": "決済条件",
  "remarks": "備考"
}}
```

注意:
- 工事名は正式名称を抽出してください（例: 都立山崎高校仮設校舎 都市ガス設備工事）
- リース期間は期間と有効期間を含めてください
- 決済条件がない場合は「本紙記載内容のみ有効とする。」とデフォルト設定してください
- 備考には法定福利費の情報などを含めてください
"""

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=2000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )

            content = response.content[0].text
            logger.debug(f"Claude response: {content}")

            # JSON部分を抽出
            import json
            import re

            json_match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                extracted_data = json.loads(json_match.group(1))
            else:
                # JSONブロックがない場合、全体をJSONとしてパース
                extracted_data = json.loads(content)

            # ProjectInfoを更新
            updated_info = existing_info.model_copy()

            # 抽出されたデータで更新（既存の値を優先、新しい情報で補完）
            if extracted_data.get("project_name") and not existing_info.project_name:
                updated_info.project_name = extracted_data["project_name"]

            if extracted_data.get("location"):
                updated_info.location = extracted_data["location"]

            if extracted_data.get("contract_period"):
                updated_info.contract_period = extracted_data["contract_period"]

            # payment_terms と remarks はProjectInfoスキーマにないので、
            # metadataまたは別のフィールドに格納する必要がありますが、
            # とりあえず、これらは表示用に別途処理します

            logger.info("Project information extracted successfully")
            logger.info(f"  Project: {updated_info.project_name}")
            logger.info(f"  Location: {updated_info.location}")
            logger.info(f"  Contract Period: {updated_info.contract_period}")

            return updated_info, extracted_data.get("payment_terms"), extracted_data.get("remarks")

        except Exception as e:
            logger.error(f"Failed to extract project info: {e}")
            return existing_info, None, None
