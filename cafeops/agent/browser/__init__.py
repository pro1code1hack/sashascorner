"""Browser ordering agents: Anthropic's browser toolset driving a Playwright page.

docs/agents/BROWSER-ORDERING.md. Modules: `types` (contracts), `executor` (Playwright),
`policy` (what may be clicked or visited), `loop` (the Messages API conversation),
`session` (persistent profiles and sign-in transport), `job` (a STAGE_BASKET run
end to end), `worker` (the queue consumer process).
"""
