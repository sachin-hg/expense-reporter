# Expense Reporter

Automated monthly expense report generator. Reads credit card / UPI statement PDFs from Gmail,
classifies transactions, and pushes a markdown report to GitHub.

## Automated monthly task

When invoked automatically (on the 8th of each month), run:

```bash
cd /Users/sachinagrawal/ai-agents/expense-reporter
python expense_reporter.py
```

This generates the report for the previous calendar month and pushes it to sachin-hg/expense-reporter.
If the push fails, report the local path and the error.

## Project layout

```
expense_reporter.py      Main CLI entry point
src/
  email_fetcher.py       IMAP Gmail → PDF download
  statement_processor.py Claude API → transaction extraction (per PDF)
  classifier.py          Claude API → category classification (all transactions)
  report_generator.py    Builds the .md + .json sidecar
  github_pusher.py       git add / commit / push
config/
  accounts.json          Email accounts config (gitignored, copy from accounts.json.example)
data/                    Temp PDF cache (gitignored)
reports/                 Monthly reports committed to the repo
```

## Setup (one-time)

1. Copy config: `cp config/accounts.json.example config/accounts.json` then fill in emails.
2. Copy env: `cp .env.example .env` then fill in API keys and Gmail app passwords.
3. Generate Gmail app passwords:  
   Google Account → Security → 2-Step Verification → App passwords → select "Mail" → generate.
4. Install deps: `pip install -r requirements.txt`
5. Ensure this directory is a git repo linked to sachin-hg/expense-reporter:
   ```
   git init
   git remote add origin https://github.com/sachin-hg/expense-reporter.git
   git pull origin main
   ```

## Running manually

```bash
python expense_reporter.py                  # previous month (default)
python expense_reporter.py --month 2026-04  # specific month
python expense_reporter.py --no-push        # don't push to GitHub
python expense_reporter.py --skip-fetch     # use PDFs already in data/
```

## Categories

| Category | What's included |
|----------|----------------|
| Travel | Fuel (HP, IOCL, BPCL), FastTag, toll, parking (Kuleep Pal payments in multiples of ₹60) |
| Trip | Flights (IndiGo, Air India…), hotels, MakeMyTrip, Cleartrip, IRCTC |
| Shopping | Myntra, Nykaa, Ajio, Amazon/Flipkart (non-grocery/baby) |
| Baby | FirstCry, Cloudnine, baby brands, diapers, pediatric visits |
| Outside Food | Swiggy, Zomato, restaurants, cafes |
| Grocery | BigBasket, Zepto, Blinkit, DMart, supermarkets |
| Utilities & Bills | DHBVN, MCG, electricity, water, gas, broadband, tax |
| Medicine | Pharmacy, non-baby clinics/hospitals, diagnostics |
| Others | Everything else |

## Report format

Reports are saved as `reports/YYYY-MM.md` with:
- Summary table (current month vs. previous 2 months)
- Per-category detail table (date | merchant | amount)
- A `reports/YYYY-MM.json` sidecar for comparison lookups

## Notes

- Transactions are filtered to the exact calendar month — statements covering partial months are handled.
- Paytm and HDFC statement formats differ significantly; Claude handles each bank's layout.
- The special parking rule: any payment to "Kuleep Pal" → Travel (parking), regardless of amount.
