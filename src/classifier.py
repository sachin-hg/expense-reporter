"""Classifies transactions into expense categories using Claude API."""

import json
import re
from typing import List
import anthropic
import logging

from .statement_processor import Transaction

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"

CATEGORIES = [
    "travel",
    "trip",
    "shopping",
    "baby",
    "outside_food",
    "grocery",
    "utilities",
    "medicine",
    "others",
]

CLASSIFICATION_RULES = """\
Classify each Indian bank/UPI transaction into exactly one category. Rules:

TRAVEL: Fuel stations (HP, Indian Oil, BPCL, IOCL, Shell, Bharat Petroleum), FastTag, toll plazas.
  SPECIAL RULE — Any payment to a person named "Kuleep Pal" (or similar spelling like Kuldeep Pal,
  Kulip Pal) → ALWAYS classify as travel (parking). Also any amount that is a multiple of 60
  paid to this person is parking.

TRIP: Airlines (IndiGo, Air India, SpiceJet, Vistara, Akasa, GoAir, GoFirst), hotels (Marriott,
  OYO, Taj, ITC, Leela, Hilton, Hyatt, Radisson), Airbnb, travel booking platforms
  (MakeMyTrip, Cleartrip, Yatra, booking.com, IRCTC, redBus).

SHOPPING: Myntra, Nykaa, Ajio, Meesho, Snapdeal, Tata CLiQ, Lifestyle, Westside, Zara, H&M,
  Reliance Trends, fashion/apparel, cosmetics/beauty (non-baby), electronics (Croma, Vijay Sales),
  Amazon or Flipkart purchases that are clearly non-grocery and non-baby.

BABY: FirstCry, Cloudnine (hospital or store), Hopscotch, Mothercare, Mamas & Papas, Nuby,
  any brand clearly for infants/toddlers, diapers (Pampers, Huggies, MamyPoko), baby formula,
  baby care products, pediatric doctor/clinic visits, baby medicines.

OUTSIDE_FOOD: Swiggy, Zomato, Dunzo (food orders), restaurants, cafes, dhabas, cloud kitchens,
  Starbucks, McDonald's, KFC, Domino's, Pizza Hut, Subway, Barbeque Nation, food courts.

GROCERY: BigBasket, Zepto, Blinkit, Instamart (Swiggy grocery), Grofers, DMart, More Supermarket,
  Reliance Fresh, Nature Basket, JioMart, Lulu hypermarket, Spar, local kiryana stores,
  any supermarket/grocery store purchase.

UTILITIES: DHBVN (Dakshin Haryana Bijli Vitran Nigam), MCG (Municipal Corporation of Gurugram),
  BSES, Tata Power, Adani Electricity, any state electricity board, water board / JJM / JJB,
  piped gas (Mahanagar Gas MGL, IGL Indraprastha Gas, Adani Gas), broadband/internet bills
  (Airtel, Jio, ACT), property tax, MCG payments, government fee payments.

MEDICINE: MedPlus, Netmeds, 1mg, Tata 1mg, Apollo Pharmacy, PharmEasy, any pharmacy or chemist,
  doctor consultations (non-pediatric), diagnostic labs (Dr. Lal Pathlabs, SRL, Thyrocare),
  hospitals that are NOT Cloudnine and NOT baby-related.

OTHERS: Insurance premiums, bank charges/fees, subscriptions (Netflix, Spotify, Prime, Hotstar),
  gym/fitness, education, mutual fund/SIP, transfers between accounts, ATM withdrawals, gifts,
  anything not clearly fitting above.

Also return a shortened merchant name (merchant_short) — a clear, brief name a human would recognise
(e.g. "Swiggy" not "SWIGGY INTERNET PVT LTD", "Zepto" not "KIRANAKART TECHNOLOGIES", etc.)."""

CLASSIFICATION_SYSTEM = (
    "You are an expert Indian expense classifier. Classify transactions precisely following the rules. "
    "Return only valid JSON — no markdown, no explanation."
)


class ExpenseClassifier:
    def __init__(self):
        self.client = anthropic.Anthropic()

    def classify(self, transactions: List[Transaction]) -> List[Transaction]:
        if not transactions:
            return transactions

        txn_list = [
            {
                "id": i,
                "date": t.date.isoformat(),
                "merchant": t.merchant,
                "description": t.description,
                "amount": t.amount,
                "bank": t.source_bank,
            }
            for i, t in enumerate(transactions)
        ]

        # Batch in chunks of 80 to stay comfortably within context
        chunk_size = 80
        for start in range(0, len(txn_list), chunk_size):
            chunk = txn_list[start : start + chunk_size]
            try:
                classifications = self._classify_batch(chunk)
                for item in classifications:
                    idx = item.get("id")
                    if idx is not None and 0 <= idx < len(transactions):
                        transactions[idx].category = item.get("category", "others")
                        transactions[idx].merchant_short = item.get("merchant_short", "")
            except Exception as e:
                logger.error(f"Classification batch failed (ids {start}–{start+len(chunk)}): {e}")

        for t in transactions:
            if not t.category:
                t.category = "others"
            if not t.merchant_short:
                t.merchant_short = t.merchant

        return transactions

    def _classify_batch(self, transactions: list) -> list:
        prompt = f"""{CLASSIFICATION_RULES}

Categories: {", ".join(CATEGORIES)}

Return JSON:
{{
  "classifications": [
    {{"id": 0, "category": "outside_food", "merchant_short": "Swiggy"}},
    ...
  ]
}}

Transactions to classify:
{json.dumps(transactions, indent=2)}"""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=CLASSIFICATION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return data.get("classifications", [])
