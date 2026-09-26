"""Payment reports: what the cafe actually took, by day and method.

The owner's architecture sketch lists "payment reports" under the Lightspeed box.
Nothing in the written brief covers it -- spec 4.5 models what was *ordered*, never
what was *settled* -- so this is the one piece of the sketches with no counterpart
in the rest of the system, and the missing half of `Money & P&L`.

Built CSV-first for the same reason channels was (spec 4.6): the Lightspeed payments
endpoint has never been probed and its shape is unknown. Guessing it would produce a
mapper that silently mis-reads real money, which is the one class of error worth
refusing outright. A back-office export is the path that works today; `PaymentSource`
leaves room for an API implementation when someone has credentials to probe with.
"""

from cafeops.integrations.payments.base import (
    PaymentDayRow,
    PaymentReport,
    PaymentSource,
    PaymentSourceUnavailable,
)
from cafeops.integrations.payments.browser_source import (
    BACK_OFFICE_PLAN,
    BrowserAgentPaymentSource,
    BrowserDriver,
    FixtureBrowserDriver,
    TakingsPlan,
)
from cafeops.integrations.payments.csv_source import CsvPaymentSource, payments_dir

__all__ = [
    "BACK_OFFICE_PLAN",
    "BrowserAgentPaymentSource",
    "BrowserDriver",
    "CsvPaymentSource",
    "FixtureBrowserDriver",
    "PaymentDayRow",
    "PaymentReport",
    "PaymentSource",
    "PaymentSourceUnavailable",
    "TakingsPlan",
    "payments_dir",
]
