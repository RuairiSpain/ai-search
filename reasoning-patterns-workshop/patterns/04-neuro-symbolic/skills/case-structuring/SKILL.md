---
name: case-structuring
description: How to turn a messy onboarding request into the structured case
  fields the control engine needs. Use for ANY customer onboarding, KYC, or
  account-opening request, even when the request is informal or incomplete.
---

# Structuring an onboarding case

Extract exactly these fields (null when genuinely unknown — never guess):
- `risk_score` (0-100, from the stated assessment; absent means null)
- `pep` (bool: any mention of political exposure/public office)
- `exposure_eur` (number: largest single-transaction or facility amount)
- `jurisdiction` (customer's operating jurisdiction code/name as given)
- `id_verified` (bool: identity verification explicitly completed?)

Unknown ≠ false: an unverified identity is `id_verified: false`, but an
*unmentioned* risk score is null, and the engine treats missing data
conservatively. Say which fields were missing in your output.
