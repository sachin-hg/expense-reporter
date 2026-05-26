---
name: classify-expenses
description: Re-classify transactions from an existing report or transaction list
---

Re-run expense classification on transactions.

This is useful when:
- You want to review/fix classifications for a specific month
- You've added new category rules and want to re-process

Steps:
1. Ask the user which month's data to re-classify.
2. Load the existing processed transactions (from the month's .json sidecar or re-extract from cached PDFs).
3. Run the classifier:

```python
from src.classifier import ExpenseClassifier
classifier = ExpenseClassifier()
classified = classifier.classify(transactions)
```

4. Show the user a summary of how transactions were classified.
5. Ask if they want to regenerate the report with the new classifications.
6. If yes, run the report generator and optionally push to GitHub.

Category rules are defined in `src/classifier.py` in the `CLASSIFICATION_RULES` constant.
To permanently change a rule, edit that constant and re-run classification.
