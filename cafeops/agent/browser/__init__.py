"""Browser ordering agents: Anthropic's browser toolset driving a Playwright page.

docs/agents/BROWSER-ORDERING.md. Modules: `types` (contracts), `executor` (Playwright),
`policy` (what may be clicked or visited), `loop` (the Messages API conversation),
`session` (persistent profiles and sign-in transport), `job` (a STAGE_BASKET run
end to end), `report` (the snapshot's warnings, proposal and audit rows: no browser,
shared with the tier 0 cart-link path in `services/browser_jobs.py`), `worker` (the
queue consumer process).
"""
