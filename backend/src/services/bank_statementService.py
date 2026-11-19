from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

PYTHON_API_URL = "http://localhost:8001"


class BankStatementService:
    def __init__(self) -> None:
        self.python_api_url = PYTHON_API_URL

    @staticmethod
    def generate_hash(file_url: str, file_name: str) -> str:
        combined_string = f"{file_url}:{file_name}"
        return hashlib.md5(combined_string.encode("utf-8")).hexdigest()

    async def process_statement(self, file_url: str, file_name: str, file_type: str) -> Dict[str, Any]:
        document_hash = self.generate_hash(file_url, file_name)

        # In the migration layer, the service remains conservative and returns the
        # payload rather than pretending a database cache exists.
        return {
            "documentHash": document_hash,
            "fileUrl": file_url,
            "fileName": file_name,
            "fileType": file_type,
            "status": "completed",
        }


bank_statement_service = BankStatementService()
