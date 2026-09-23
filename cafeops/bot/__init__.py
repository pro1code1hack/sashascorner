"""The Telegram bot: the café owner's daily interface, in Russian.

Spec 10: the bot is what gets touched every day. The web app is for thinking about the
business; this is for running it. Six flows and nothing else -- a morning digest, order
confirmation, two kinds of count, the tier C checklist and delivery receipt.

Module map, and why the split is this shape:

* `formatters.py` -- **every** user-facing string, in Russian. The only place.
* `viewmodels.py` -- what is shown, as numbers and enums. No text at all.
* `views.py` -- reads the database through the services layer, classifies English prose
  into enums, returns viewmodels. Synchronous.
* `handlers/` -- async aiogram handlers. Thin: they reach `views` through
  `deps.run_sync`, render with `formatters`, and hold no logic of their own.
* `keyboards.py`, `callbacks.py`, `states.py` -- the inline UI, typed.
* `notify.py` -- how a scheduled job reaches the owner when there is no `Message` to
  answer.
* `preview.py` -- drives the real dispatcher locally with no Telegram, so the Russian can
  be read and checked. There is no bot token, so this is how the bot is verified.
"""
