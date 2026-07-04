"""
Google Sheets connector via gspread + OAuth2 service account.
The service account JSON is stored as a string in ConnectorConfig (base64 or inline JSON).
"""

from __future__ import annotations

import asyncio
import json

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from connectors.base import DataConnector

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


class SheetsConnector(DataConnector):
    def _client(self) -> gspread.Client:
        sa_json = self.config.service_account_json
        if not sa_json:
            raise ValueError("service_account_json is required for Sheets connector")
        info = json.loads(sa_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)

    async def load(self) -> pd.DataFrame:
        spreadsheet_id = self.config.spreadsheet_id
        sheet_name = self.config.sheet_name

        if not spreadsheet_id:
            raise ValueError("spreadsheet_id is required for Sheets connector")

        # BUG-08: Wrap sync gspread I/O in to_thread to avoid blocking event loop
        def _sync_load():
            gc = self._client()
            spreadsheet = gc.open_by_key(spreadsheet_id)
            worksheet = spreadsheet.worksheet(sheet_name) if sheet_name else spreadsheet.sheet1
            return worksheet.get_all_records()

        records = await asyncio.to_thread(_sync_load)
        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df = self._coerce_numeric(df)
        return df

    async def test_connection(self) -> tuple[bool, str]:
        try:
            # BUG-08: Wrap sync gspread I/O in to_thread
            def _sync_test():
                gc = self._client()
                gc.open_by_key(self.config.spreadsheet_id)
            await asyncio.to_thread(_sync_test)
            return True, "Spreadsheet accessible"
        except Exception as e:
            return False, str(e)

    def _coerce_numeric(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="ignore")
        return df
