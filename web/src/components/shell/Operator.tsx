/**
 * "Who's using this?" affordances (DECISIONS §6, stock-orders-suppliers.md C10).
 *
 * - `OperatorControl`: the sidebar line. Shows the name, or asks for one;
 *   editable inline.
 * - `OperatorNeeded`: drop it next to a write that needs a name. Renders
 *   nothing once a name is set; otherwise a one-line inline form, so the
 *   person never has to leave the screen to unblock the button.
 *
 * Screens read the name with `useOperator()` from lib/operator and pass it as
 * `counted_by` / `received_by` / `responded_by` / `decided_by` / `actor`.
 * Any write that needs it stays disabled while it is null.
 */
/** Removed by owner's instruction (2026-09-26): the app never asks who you are. */
export function OperatorControl() {
  return null
}

/** Kept as a no-op so existing call sites compile; renders nothing. */
export function OperatorNeeded(_props: { what: string }) {
  return null
}
