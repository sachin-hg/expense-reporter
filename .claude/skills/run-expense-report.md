---
name: run-expense-report
description: Run the full monthly expense report workflow (fetch emails, process PDFs, classify, generate report, push to GitHub)
---

Run the monthly expense report workflow.

1. Ask the user: which month? (default: previous month in YYYY-MM format)
2. Ask: push to GitHub? (default: yes)
3. Run the appropriate command:

```bash
cd /Users/sachinagrawal/ai-agents/expense-reporter
python expense_reporter.py [--month YYYY-MM] [--no-push]
```

4. Monitor output and report any errors.
5. If successful, confirm the report path and (if pushed) that it's live on GitHub.

If the user says "use cached PDFs" or "skip email fetch", add `--skip-fetch` to the command.
