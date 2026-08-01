---
name: standard-ledger
description: Parse a standard debit/credit CSV ledger and reconcile totals.
  Apply for any month-end close over a standard CSV ledger.
---

# Standard ledger parsing
- Format: CSV with columns account, debit, credit (both non-negative).
- Net = credit - debit per row; totals must reconcile to zero across the
  P&L account set.
- Report each account's net in the deliverable.
