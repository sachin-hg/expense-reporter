"""Processes bank statement PDFs using Claude API to extract transactions."""

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional
import anthropic
import logging

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"


@dataclass
class Transaction:
    date: date
    merchant: str
    amount: float           # positive = spend/debit, negative = refund/credit
    description: str
    source_bank: str
    source_account: str
    category: str = ""
    merchant_short: str = ""


BANK_HINTS = {
    "HDFC Infinia": (
        "HDFC Bank Infinia Credit Card statement. "
        "Extract all debit/purchase transactions from the 'Domestic Transactions' and "
        "'International Transactions' sections. The Debit column shows spend amounts."
    ),
    "HDFC Swiggy": (
        "HDFC Bank Swiggy Credit Card statement. "
        "Extract all purchase/debit transactions listed in the statement period."
    ),
    "HDFC": (
        "HDFC Bank Credit Card statement. "
        "Extract all purchase/debit transactions."
    ),
    "ICICI": (
        "ICICI Bank Credit Card statement. "
        "Look for the transaction details table. Debit entries represent spends."
    ),
    "Paytm": (
        "Paytm payment statement. "
        "Extract all outgoing payments: UPI transfers, wallet debits, bill payments. "
        "Ignore incoming credits/refunds."
    ),
    "SBI": "SBI Credit Card statement. Extract all debit purchase transactions.",
    "Axis": "Axis Bank Credit Card statement. Extract all debit purchase transactions.",
    "Kotak": "Kotak Bank Credit Card statement. Extract all debit purchase transactions.",
}

EXTRACTION_PROMPT = """\
Extract ALL debit/spend transactions from this {bank} statement.

{hint}

Return JSON in exactly this format (no markdown fences):
{{
  "statement_period": {{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}},
  "transactions": [
    {{
      "date": "YYYY-MM-DD",
      "merchant": "Merchant or payee name as shown in statement",
      "description": "Full transaction description",
      "amount": 1234.56
    }}
  ]
}}

Rules:
- amount must be a positive number (the rupee spend amount, no symbols)
- Skip: credit entries, refunds, reversals, cashbacks, opening/closing balances, fee reversals
- Include: all purchases, payments, EMI debits, fee charges
- date must be YYYY-MM-DD
- Extract every single debit row — do not summarize or group
- merchant: use the name shown in the statement, not your own knowledge of the business"""


class StatementProcessor:
    def __init__(self):
        self.client = anthropic.Anthropic()

    def process(self, pdf_result) -> List[Transaction]:
        """Extract transactions from a PDF statement."""
        bank = self._detect_bank(pdf_result.subject)
        hint = BANK_HINTS.get(bank, "Extract all debit spend transactions from this bank statement.")
        prompt = EXTRACTION_PROMPT.format(bank=bank, hint=hint)

        pdf_b64 = base64.standard_b64encode(pdf_result.bytes).decode("utf-8")

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=8096,
            system=(
                "You are a financial data extraction specialist. Extract transactions from bank "
                "statements with perfect accuracy. Return only valid JSON — no markdown, no explanation."
            ),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        transactions = []

        for t in data.get("transactions", []):
            try:
                txn_date = self._parse_date(t.get("date", ""))
                if txn_date is None:
                    logger.warning(f"Skipping — unparseable date: {t}")
                    continue

                amount_raw = str(t.get("amount", 0)).replace(",", "").strip()
                amount = float(amount_raw)
                if amount <= 0:
                    continue

                transactions.append(
                    Transaction(
                        date=txn_date,
                        merchant=t.get("merchant") or t.get("description", "Unknown"),
                        amount=amount,
                        description=t.get("description", ""),
                        source_bank=bank,
                        source_account=pdf_result.account,
                    )
                )
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Skipping transaction {t}: {e}")

        return transactions

    def _detect_bank(self, subject: str) -> str:
        s = subject.lower()
        if "paytm" in s:
            return "Paytm"
        if "icici" in s:
            return "ICICI"
        if "hdfc" in s and "swiggy" in s:
            return "HDFC Swiggy"
        if "hdfc" in s and "infinia" in s:
            return "HDFC Infinia"
        if "hdfc" in s:
            return "HDFC"
        if "sbi" in s:
            return "SBI"
        if "axis" in s:
            return "Axis"
        if "kotak" in s:
            return "Kotak"
        return "Unknown"

    def _parse_date(self, date_str: str) -> Optional[date]:
        if not date_str:
            return None
        for fmt in (
            "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
            "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
        ):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        logger.warning(f"Could not parse date: {date_str!r}")
        return None
