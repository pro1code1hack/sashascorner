"""Wallet passes for Sasha's Corner Rewards: Apple PassKit and Google Wallet.

One customer, one card, one serial (docs/loyalty/SPEC.md "Wallet passes"). The server is
the single source of truth; a pass is only ever a *rendering* of `CardView`, rebuilt on
demand. Nothing here decides a stamp count -- it reads the one `services/loyalty` computed.

Absent credentials is a supported state, like Lightspeed's: `build_pkpass` / `save_url`
raise `WalletNotConfigured`, the join flow offers the web card instead, and the push
outbox marks rows done with the reason rather than retrying forever.

Deliberately imports nothing at package level: the API process imports `public_routes`
and `apple_webservice`, the CLI imports `cli`, and neither should pay for the other.
"""
