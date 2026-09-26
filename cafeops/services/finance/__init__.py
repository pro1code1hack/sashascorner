"""Finance ("Money"): trading days, expenses, cash, payouts, delivery statements, P&L,
director's account. docs/design/specs/finance.md 3.5.

All finance writes go through these modules (CLAUDE.md 8). Submodules are imported
directly (`from cafeops.services.finance.expenses import ...`); this package imports
nothing so `services.ingest_payments` can use `takings` without a cycle.
"""
