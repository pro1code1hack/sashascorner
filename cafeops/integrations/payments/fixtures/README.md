# Sample payment exports

These are **shaped like** a back-office payment report, not taken from one — the
Lightspeed payments endpoint has never been probed and no real export has been seen.
They exist so the reader can be exercised and the Money screen can be built against
something, exactly as `integrations/channels/fixtures` did before real exports arrived.

Replace them with a real export (or point `CAFEOPS_PAYMENTS_CSV_DIR` at one) and the
column mapper will either map it or refuse it by name. It will not guess.
