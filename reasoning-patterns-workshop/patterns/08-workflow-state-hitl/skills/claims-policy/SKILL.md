---
name: claims-policy
description: Contoso claims handling limits, exception triggers and payment
  rules. Consult for ANY claim assessment, coverage question, or payment
  decision, even when limits aren't mentioned in the request.
---

# Contoso Claims Policy CL-4 (fictional)

## Coverage
- Covered incident types: collision, theft, weather, glass.
- Not covered: wear_and_tear, undeclared_commercial_use.

## Limits and exception triggers
- Claims up to EUR 2,500 with complete data and covered type: straight-through.
- EUR 2,500-25,000: exception — human review required.
- Above EUR 25,000: exception — human review + senior sign-off.
- ANY third-party involvement: exception regardless of amount.
- Incomplete data: hold (never pay, never decline) until fields arrive.

## Payment
Payment is executed by the rules engine only, after the router permits it.
No agent may pay, and no narrative may raise a limit.
