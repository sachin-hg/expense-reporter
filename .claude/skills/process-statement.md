---
name: process-statement
description: Process a single bank statement PDF and extract transactions using Claude
---

Process a specific bank statement PDF to extract transactions.

1. Ask which PDF file to process (or use the one the user provides/mentions).
2. Load the PDF and detect the bank from the filename or user's description.
3. Run the statement processor:

```python
import base64
from src.statement_processor import StatementProcessor
from src.email_fetcher import PDFResult
from datetime import date

pdf_bytes = open("path/to/statement.pdf", "rb").read()
result = PDFResult(bytes=pdf_bytes, subject="email subject here", email_date=date.today(), account="manual")
processor = StatementProcessor()
transactions = processor.process(result)
for t in transactions:
    print(f"{t.date} | {t.merchant:40s} | ₹{t.amount:,.0f}")
print(f"\nTotal: {len(transactions)} transactions")
```

4. Display the extracted transactions in a table.
5. Ask if the user wants to classify them or save them.

Use `--skip-fetch` mode in expense_reporter.py if you want to process PDFs you've manually put in `data/`.
