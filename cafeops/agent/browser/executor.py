"""PlaywrightExecutor: every `browser_toolset_20260801` member against a real page.

docs/agents/BROWSER-ORDERING.md §3 and §5. The executor owns one Playwright
persistent context (one Chromium profile per supplier), keeps the tab inventory and
the element refs, and turns each member call into the content blocks the Messages
API expects back in the `tool_result`. It knows nothing about orders or models.

Two things are enforced here rather than in the loop, because a prompt is not a
boundary (invariant 1):

* `navigate` refuses any URL that is not http/https, whose host is outside
  `PortalPolicy.allowed_hosts`, or that matches `forbidden_url_patterns`.
* A context-wide route answers every *document* request (top-level and frame
  navigations) that fails the same checks with `204 No Content`, which the browser
  treats as "stay where you are": a click on an off-site link, a redirect or a
  `target=_blank` popup cannot leave the allowlist either. Subresources (CDNs,
  images, scripts) are not filtered.

Coordinates: the model sees screenshots downscaled so the long side is at most
`screenshot_max_px`, and every coordinate it sends is in that space. The executor
scales them back to viewport pixels before dispatch; the viewport itself never
changes during a session.

Refs: `data-cafeops-ref="ref_N"` attributes stamped by `snapshot.py`. Numbers only
go up within a tab; a navigation clears the map, so a ref from a previous document
is reported stale rather than hitting a new element that happens to share a number.

Known limits, on purpose: iframes are not walked (a ref never crosses a frame);
`storage_state` seeding restores cookies fully and localStorage on the first load of
each origin; downloads are reported as `download_started` only; the four opt-in
members (`javascript_exec`, `file_upload`, `read_console`, `read_network`) are
disabled and say so.
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import (
    BrowserContext,
    Download,
    Frame,
    Locator,
    Page,
    Playwright,
    Request,
    Route,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError

from cafeops.agent.browser.snapshot import (
    CONTROL_KIND_JS,
    DESCRIBE_JS,
    PAGE_TEXT_JS,
    SNAPSHOT_JS,
)
from cafeops.agent.browser.types import ActionResult, BrowserAction, ElementInfo
from cafeops.integrations.suppliers.portals.base import PortalPolicy

ACTION_TIMEOUT_MS = 10_000
NAVIGATION_TIMEOUT_MS = 30_000
SETTLE_TIMEOUT_MS = 3_000
MAX_TEXT_CHARS = 50_000
FIND_LIMIT = 20
SCROLL_PX_PER_UNIT = 100

DISABLED_MEMBERS = frozenset({"javascript_exec", "file_upload", "read_console", "read_network"})

_REF_RE = re.compile(r"^ref_\d+$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
# Schemes a URL may genuinely start with. Anything else before a colon is a host with
# a port ("localhost:8000/x") and gets https:// in front; these are refused as-is.
_KNOWN_SCHEMES = frozenset(
    {
        "http",
        "https",
        "file",
        "javascript",
        "data",
        "blob",
        "about",
        "chrome",
        "chrome-extension",
        "chrome-error",
        "devtools",
        "view-source",
        "mailto",
        "tel",
        "sms",
        "ftp",
        "ftps",
        "sftp",
        "ws",
        "wss",
        "vbscript",
        "intent",
        "filesystem",
    }
)

# "ctrl" is the model's generic accelerator: Control on the Linux worker, Meta on a
# macOS laptop running `cafeops portal connect`. Playwright's ControlOrMeta is both.
_MODIFIER_NAMES: dict[str, str] = {
    "ctrl": "ControlOrMeta",
    "control": "ControlOrMeta",
    "shift": "Shift",
    "alt": "Alt",
    "option": "Alt",
    "meta": "Meta",
    "cmd": "Meta",
    "command": "Meta",
    "win": "Meta",
    "super": "Meta",
}

_KEY_NAMES: dict[str, str] = {
    **_MODIFIER_NAMES,
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "space": "Space",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "page_up": "PageUp",
    "pagedown": "PageDown",
    "page_down": "PageDown",
    "up": "ArrowUp",
    "arrowup": "ArrowUp",
    "down": "ArrowDown",
    "arrowdown": "ArrowDown",
    "left": "ArrowLeft",
    "arrowleft": "ArrowLeft",
    "right": "ArrowRight",
    "arrowright": "ArrowRight",
    "capslock": "CapsLock",
    "numlock": "NumLock",
    "scrolllock": "ScrollLock",
    "printscreen": "PrintScreen",
    "pause": "Pause",
    "contextmenu": "ContextMenu",
    "plus": "+",
    "minus": "-",
    **{f"f{i}": f"F{i}" for i in range(1, 25)},
}

_CLICK_BUTTONS: dict[str, Literal["left", "middle", "right"]] = {
    "right_click": "right",
    "middle_click": "middle",
}

_CLICK_VERBS = {
    "left_click": "Clicked",
    "right_click": "Right-clicked",
    "middle_click": "Middle-clicked",
    "double_click": "Double-clicked",
    "triple_click": "Triple-clicked",
}


class _StaleRef(Exception):
    def __init__(self, ref: str) -> None:
        super().__init__(ref)
        self.ref = ref


class _Refused(Exception):
    """A member call that must come back as `is_error` with this exact text."""


class _Disabled(_Refused):
    """An opt-in member this deployment keeps off; the text carries no prefix."""


def _stale_text(ref: str) -> str:
    return (
        f"Error: {ref} is stale or not found on the current page. "
        "Re-read the page to get fresh references."
    )


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else exc.__class__.__name__


@dataclass(slots=True)
class _Tab:
    tab_id: str
    page: Page
    #: Highest ref number issued in this tab, across documents.
    seq: int = 0
    #: What the last read_page/find said about each ref (for summaries; liveness is
    #: always re-checked against the DOM).
    refs: dict[str, ElementInfo] = field(default_factory=dict)


class PlaywrightExecutor:
    """Sync `BrowserExecutor` over a persistent Chromium profile.

    Build with `launch(...)`; use as a context manager or call `close()`.
    """

    def __init__(
        self,
        playwright: Playwright,
        context: BrowserContext,
        *,
        policy: PortalPolicy,
        screenshot_max_px: int,
        viewport: tuple[int, int],
    ) -> None:
        self._pw = playwright
        self._context = context
        self._policy = policy
        self._max_px = max(64, int(screenshot_max_px))
        self._viewport = (int(viewport[0]), int(viewport[1]))
        # Viewport pixels per screenshot pixel. >= 1: screenshots are never upscaled.
        self._scale = max(1.0, max(self._viewport) / self._max_px)
        self._tabs: list[_Tab] = []
        self._active: _Tab | None = None
        self._next_tab_no = 1
        self._pending_changes: list[dict[str, Any]] = []
        self._blocked: list[str] = []
        self._closing = False

        context.set_default_timeout(ACTION_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        context.route("**/*", self._route)
        for page in context.pages:
            self._adopt(page, announce=False)
        context.on("page", self._on_new_page)
        if self._active is None:
            self._adopt(context.new_page(), announce=False)

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def launch(
        cls,
        *,
        profile_dir: Path,
        policy: PortalPolicy,
        headless: bool = True,
        screenshot_max_px: int = 1280,
        viewport: tuple[int, int] = (1280, 900),
        downloads_dir: Path | None = None,
        storage_state: dict[str, Any] | None = None,
    ) -> PlaywrightExecutor:
        """Open the profile at `profile_dir` (created if missing) and return an
        executor with one blank tab. `storage_state` (a Playwright storage state:
        `{"cookies": [...], "origins": [...]}`) seeds the profile: cookies are added
        directly, localStorage is written on the first load of each origin."""
        profile_dir = Path(profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        if downloads_dir is not None:
            Path(downloads_dir).mkdir(parents=True, exist_ok=True)
        pw = sync_playwright().start()
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                viewport={"width": int(viewport[0]), "height": int(viewport[1])},
                device_scale_factor=1,
                accept_downloads=downloads_dir is not None,
                downloads_path=str(downloads_dir) if downloads_dir is not None else None,
                args=["--disable-blink-features=AutomationControlled"],
                locale="en-GB",
                timezone_id="Europe/London",
            )
        except Exception:
            pw.stop()
            raise
        try:
            if storage_state:
                cls._seed_storage(context, storage_state)
            return cls(
                pw,
                context,
                policy=policy,
                screenshot_max_px=screenshot_max_px,
                viewport=viewport,
            )
        except Exception:
            context.close()
            pw.stop()
            raise

    @staticmethod
    def _seed_storage(context: BrowserContext, state: dict[str, Any]) -> None:
        cookies = state.get("cookies") or []
        if cookies:
            context.add_cookies(cookies)
        by_origin: dict[str, list[dict[str, str]]] = {}
        for origin in state.get("origins") or []:
            items = origin.get("localStorage") or []
            if origin.get("origin") and items:
                by_origin[str(origin["origin"])] = [
                    {"name": str(i.get("name", "")), "value": str(i.get("value", ""))}
                    for i in items
                ]
        if by_origin:
            context.add_init_script(
                script=(
                    "(() => { const data = "
                    + json.dumps(by_origin)
                    + "; const items = data[location.origin]; if (!items) return;"
                    " try { if (localStorage.getItem('__cafeops_seeded')) return;"
                    " for (const it of items) localStorage.setItem(it.name, it.value);"
                    " localStorage.setItem('__cafeops_seeded', '1'); } catch (e) {} })();"
                )
            )

    def export_storage_state(self) -> dict[str, Any]:
        """Cookies and localStorage of the profile, as Playwright's storage state."""
        return dict(self._context.storage_state())

    def set_default_timeout(self, timeout_ms: int) -> None:
        self._context.set_default_timeout(int(timeout_ms))

    @property
    def page(self) -> Page:
        return self._active_tab().page

    @property
    def context(self) -> BrowserContext:
        return self._context

    @property
    def policy(self) -> PortalPolicy:
        return self._policy

    def close(self) -> None:
        self._closing = True
        try:
            self._context.close()
        except PlaywrightError:
            pass
        finally:
            self._pw.stop()

    def __enter__(self) -> PlaywrightExecutor:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------- tab bookkeeping

    def _adopt(self, page: Page, *, announce: bool) -> _Tab:
        tab = _Tab(tab_id=f"tab-{self._next_tab_no}", page=page)
        self._next_tab_no += 1
        self._tabs.append(tab)
        if self._active is None:
            self._active = tab
        page.on("framenavigated", lambda frame: self._on_navigated(tab, frame))
        page.on("close", lambda _page: self._on_closed(tab))
        page.on("download", lambda download: self._on_download(tab, download))
        if announce:
            self._pending_changes.append({"type": "tab_opened", "tab_id": tab.tab_id})
        return tab

    def _on_new_page(self, page: Page) -> None:
        if self._find_tab_by_page(page) is None:
            self._adopt(page, announce=True)

    def _on_navigated(self, tab: _Tab, frame: Frame) -> None:
        if frame.parent_frame is None:
            tab.refs.clear()

    def _on_closed(self, tab: _Tab) -> None:
        if tab in self._tabs:
            self._tabs.remove(tab)
        if self._active is tab:
            self._active = self._tabs[-1] if self._tabs else None

    def _on_download(self, tab: _Tab, download: Download) -> None:
        self._pending_changes.append(
            {
                "type": "download_started",
                "tab_id": tab.tab_id,
                "url": download.url,
                "filename": download.suggested_filename,
            }
        )

    def _active_tab(self) -> _Tab:
        if self._active is None or self._active.page.is_closed():
            live = [t for t in self._tabs if not t.page.is_closed()]
            if not live:
                if self._closing:
                    raise PlaywrightError("browser is closed")
                live = [self._adopt(self._context.new_page(), announce=True)]
            self._tabs = live
            self._active = live[-1]
        return self._active

    def _tab_for(self, action_input: dict[str, Any]) -> _Tab:
        tab_id = action_input.get("tab_id")
        if tab_id in (None, ""):
            return self._active_tab()
        for tab in self._tabs:
            if tab.tab_id == tab_id and not tab.page.is_closed():
                return tab
        raise _Refused(f"Error: unknown tab_id {tab_id!r}. Use list_tabs to see open tabs.")

    def _find_tab(self, tab_id: Any) -> _Tab | None:
        for tab in self._tabs:
            if tab.tab_id == tab_id:
                return tab
        return None

    # ------------------------------------------------------------- policy at the edge

    def _navigation_refusal(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https"):
            return "Error: Navigation refused. Only http and https URLs are allowed."
        host = parts.hostname or ""
        if not host or not self._policy.host_allowed(host):
            return (
                f"Error: Navigation refused: {host or url} is not an allowed host "
                "for this supplier."
            )
        pattern = self._policy.url_forbidden(url)
        if pattern:
            return f"Error: Navigation refused by policy: this looks like checkout ({pattern})"
        return None

    def _route(self, route: Route, request: Request) -> None:
        if request.resource_type == "document":
            reason = self._navigation_refusal(request.url)
            if reason is not None:
                # A 204 makes the browser stay exactly where it is. Aborting instead
                # would commit a chrome-error:// page, which is leaving the site too.
                self._blocked.append(request.url)
                route.fulfill(status=204, body="")
                return
        route.continue_()

    @staticmethod
    def _normalise_url(raw: str) -> str:
        url = raw.strip()
        if url.startswith("//"):
            return "https:" + url
        if not _SCHEME_RE.match(url):
            return "https://" + url
        # "localhost:8000/x" parses as scheme "localhost"; only real schemes count.
        scheme = url.split(":", 1)[0].lower()
        if scheme not in _KNOWN_SCHEMES and not url[len(scheme) + 1 :].startswith("/"):
            return "https://" + url
        return url

    # --------------------------------------------------------------- protocol API

    def current_url(self) -> str:
        return self._active_tab().page.url

    def screenshot_png(self) -> bytes:
        return self._capture(self._active_tab().page)

    def browser_state(self) -> dict[str, Any]:
        active = self._active_tab()
        tabs: list[dict[str, Any]] = []
        for tab in self._tabs:
            if tab.page.is_closed():
                continue
            try:
                title = tab.page.title()
            except PlaywrightError:
                title = ""
            tabs.append(
                {
                    "tab_id": tab.tab_id,
                    "title": title,
                    "url": tab.page.url,
                    "active": tab is active,
                }
            )
        block: dict[str, Any] = {"type": "browser_state", "tabs": tabs}
        if self._pending_changes:
            block["state_changes"] = list(self._pending_changes)
            self._pending_changes.clear()
        return block

    def describe_ref(self, ref: str) -> ElementInfo | None:
        if not _REF_RE.match(str(ref)):
            return None
        tab = self._active_tab()
        try:
            loc = self._locator(tab.page, ref)
            if loc.count() == 0:
                return None
            data = loc.evaluate(DESCRIBE_JS)
        except PlaywrightError:
            return None
        if not isinstance(data, dict):
            return None
        info = ElementInfo(
            ref=ref,
            role=data.get("role"),
            name=data.get("name"),
            tag=data.get("tag"),
            href=data.get("href"),
        )
        tab.refs[ref] = info
        return info

    def execute(self, action: BrowserAction) -> ActionResult:
        started = time.monotonic()
        self._blocked.clear()
        member = action.member
        try:
            if member in DISABLED_MEMBERS:
                raise _Disabled(f"{member} is not enabled in this environment.")
            handler = getattr(self, f"_m_{member}", None)
            if handler is None:
                raise _Refused(f"Error: unknown browser member {member!r}.")
            result: ActionResult = handler(action.input)
        except _StaleRef as exc:
            result = self._error(_stale_text(exc.ref))
        except _Disabled as exc:
            result = ActionResult(content=str(exc), is_error=True, summary=str(exc))
        except _Refused as exc:
            result = self._error(str(exc))
        except PlaywrightError as exc:
            result = self._error(f"Error: {_first_line(exc)}")
        except (KeyError, TypeError, ValueError) as exc:
            result = self._error(f"Error: bad input for {member}: {exc}")
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if result.url is None:
            try:
                result.url = self.current_url()
            except PlaywrightError:
                result.url = None
        return result

    # ------------------------------------------------------------ result builders

    def _error(self, message: str) -> ActionResult:
        text = message if message.startswith("Error") else f"Error: {message}"
        return ActionResult(content=text, is_error=True, summary=text.splitlines()[0])

    def _ok(self, text: str, *, summary: str | None = None) -> ActionResult:
        if self._blocked:
            blocked = ", ".join(dict.fromkeys(self._blocked))
            text += f" (navigation to {blocked} was blocked by policy; the page did not change)"
            self._blocked.clear()
        return ActionResult(
            content=[{"type": "text", "text": text}, self.browser_state()],
            summary=summary or text,
            url=self.current_url(),
        )

    def _state_only(self) -> ActionResult:
        state = self.browser_state()
        active = next((t for t in state["tabs"] if t["active"]), None)
        summary = f"{len(state['tabs'])} tab(s), active {active['tab_id'] if active else '-'}"
        return ActionResult(content=[state], summary=summary, url=self.current_url())

    @staticmethod
    def _image_result(png: bytes, summary: str, url: str) -> ActionResult:
        block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode("ascii"),
            },
        }
        return ActionResult(content=[block], summary=summary, screenshot_png=png, url=url)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _locator(page: Page, ref: str) -> Locator:
        return page.locator(f'[data-cafeops-ref="{ref}"]').first

    def _live_locator(self, tab: _Tab, ref: str) -> Locator:
        if not _REF_RE.match(str(ref)):
            raise _StaleRef(str(ref))
        loc = self._locator(tab.page, ref)
        if loc.count() == 0:
            raise _StaleRef(ref)
        return loc

    def _info(self, tab: _Tab, ref: str) -> ElementInfo:
        try:
            data = self._locator(tab.page, ref).evaluate(DESCRIBE_JS)
        except PlaywrightError:
            data = None
        if isinstance(data, dict):
            info = ElementInfo(
                ref=ref,
                role=data.get("role"),
                name=data.get("name"),
                tag=data.get("tag"),
                href=data.get("href"),
            )
            tab.refs[ref] = info
            return info
        return tab.refs.get(ref) or ElementInfo(ref=ref, role=None, name=None, tag=None, href=None)

    @staticmethod
    def _label(info: ElementInfo) -> str:
        role = info.role or "element"
        return f"{role} '{info.name}' [{info.ref}]" if info.name else f"{role} [{info.ref}]"

    def _coord(self, target: dict[str, Any]) -> tuple[float, float]:
        x = float(target["x"]) * self._scale
        y = float(target["y"]) * self._scale
        return x, y

    @staticmethod
    def _target(action_input: dict[str, Any], key: str = "target") -> dict[str, Any]:
        target = action_input.get(key)
        if not isinstance(target, dict) or target.get("type") not in ("ref", "coordinate"):
            raise _Refused(
                f'Error: {key!r} must be {{"type": "ref", "ref": "ref_N"}} or '
                f'{{"type": "coordinate", "x": X, "y": Y}}.'
            )
        if target["type"] == "coordinate" and ("x" not in target or "y" not in target):
            raise _Refused(f"Error: coordinate {key!r} needs x and y.")
        if target["type"] == "ref" and not target.get("ref"):
            raise _Refused(f"Error: ref {key!r} needs a ref.")
        return target

    @staticmethod
    def _modifiers(raw: Any) -> list[Any]:
        if raw in (None, ""):
            return []
        mods: list[Any] = []
        for part in str(raw).replace(" ", "").split("+"):
            if not part:
                continue
            name = _MODIFIER_NAMES.get(part.lower())
            if name is None:
                raise _Refused(f"Error: unknown modifier {part!r}; use ctrl, shift, alt or meta.")
            if name not in mods:
                mods.append(name)
        return mods

    @staticmethod
    def _key_chord(token: str) -> str:
        token = token.strip()
        if not token:
            raise _Refused("Error: key needs a key name.")
        if token == "+":
            return "+"
        parts = token.split("+")
        # "ctrl++" -> the "+" key with Control held.
        if token.endswith("+") and len(parts) >= 2 and parts[-1] == "":
            parts = [p for p in parts[:-1] if p] + ["+"]
        keys: list[str] = []
        for part in parts:
            if part == "":
                continue
            lowered = part.lower()
            if lowered in _KEY_NAMES:
                keys.append(_KEY_NAMES[lowered])
            elif len(part) == 1:
                keys.append(part)
            else:
                keys.append(part[0].upper() + part[1:])
        return "+".join(keys)

    @staticmethod
    def _duration(raw: Any, *, default: float = 1.0) -> float:
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise _Refused("Error: duration must be a number of seconds (0-30).") from exc
        return min(30.0, max(0.0, value))

    @staticmethod
    def _settle(page: Page) -> None:
        """Give a click or key press its navigation, if it started one."""
        try:
            page.wait_for_timeout(120)
            page.wait_for_load_state("load", timeout=SETTLE_TIMEOUT_MS)
        except PlaywrightError:
            pass

    def _capture(self, page: Page) -> bytes:
        raw = page.screenshot(type="png")
        if self._scale <= 1.0:
            return raw
        with Image.open(io.BytesIO(raw)) as img:
            width = max(1, round(img.width / self._scale))
            height = max(1, round(img.height / self._scale))
            out = io.BytesIO()
            img.resize((width, height), Image.Resampling.LANCZOS).save(out, format="PNG")
            return out.getvalue()

    def _snapshot(self, tab: _Tab, opts: dict[str, Any]) -> dict[str, Any]:
        opts = {**opts, "seq": tab.seq}
        data = tab.page.evaluate(SNAPSHOT_JS, opts)
        if not isinstance(data, dict):
            raise PlaywrightError("page snapshot returned nothing")
        if data.get("error") == "stale":
            raise _StaleRef(str(opts.get("ref")))
        tab.seq = max(tab.seq, int(data.get("seq") or 0))
        for node in data.get("nodes") or []:
            tab.refs[str(node["ref"])] = ElementInfo(
                ref=str(node["ref"]),
                role=node.get("role"),
                name=node.get("name"),
                tag=node.get("tag"),
                href=node.get("href"),
            )
        return data

    # ------------------------------------------------------------------ members

    # navigation & capture -------------------------------------------------

    def _m_navigate(self, action_input: dict[str, Any]) -> ActionResult:
        raw = action_input.get("url")
        if not isinstance(raw, str) or not raw.strip():
            raise _Refused("Error: navigate needs a url.")
        tab = self._tab_for(action_input)
        url = self._normalise_url(raw)
        refusal = self._navigation_refusal(url)
        if refusal is not None:
            raise _Refused(refusal)
        try:
            self._goto(tab.page, url)
        except PlaywrightError as exc:
            # A failed navigation commits chrome-error:// a moment later; let it land
            # so the next goto is not "interrupted by another navigation".
            self._settle(tab.page)
            if self._blocked or "ERR_BLOCKED_BY_CLIENT" in str(exc):
                blocked = self._blocked[-1] if self._blocked else url
                self._blocked.clear()
                raise _Refused(
                    f"Error: Navigation refused by policy: {blocked} is outside this "
                    "supplier's allowed pages."
                ) from exc
            raise _Refused(f"Error: Navigation failed: {_first_line(exc)}") from exc
        if self._blocked:
            # A redirect (or the request itself) was answered 204 by the policy route.
            blocked = self._blocked[-1]
            self._blocked.clear()
            raise _Refused(
                f"Error: Navigation refused by policy: {blocked} is outside this "
                "supplier's allowed pages."
            )
        tab.refs.clear()
        return self._ok(f"Navigated to {tab.page.url}", summary=f"navigated to {tab.page.url}")

    @staticmethod
    def _goto(page: Page, url: str) -> None:
        """`page.goto`, retried once when Chromium's late error-page commit from an
        earlier failed navigation interrupts this one (a known race)."""
        try:
            page.goto(url, wait_until="load")
        except PlaywrightError as exc:
            if "interrupted by another navigation" not in str(exc):
                raise
            page.wait_for_timeout(250)
            page.goto(url, wait_until="load")

    def _m_screenshot(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        png = self._capture(tab.page)
        with Image.open(io.BytesIO(png)) as img:
            size = f"{img.width}x{img.height}"
        return self._image_result(png, f"screenshot {size}", tab.page.url)

    def _m_zoom(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        region = action_input.get("region")
        if not isinstance(region, list | tuple) or len(region) != 4:
            raise _Refused("Error: zoom needs region [x0, y0, x1, y1].")
        x0, y0, x1, y1 = (float(v) * self._scale for v in region)
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        raw = tab.page.screenshot(type="png")
        with Image.open(io.BytesIO(raw)) as img:
            box = (
                int(max(0, min(left, img.width))),
                int(max(0, min(top, img.height))),
                int(max(0, min(right, img.width))),
                int(max(0, min(bottom, img.height))),
            )
            if box[2] - box[0] < 1 or box[3] - box[1] < 1:
                raise _Refused("Error: zoom region is empty or outside the screenshot.")
            crop = img.crop(box)
            factor = self._max_px / max(crop.width, crop.height)
            width = max(1, round(crop.width * factor))
            height = max(1, round(crop.height * factor))
            out = io.BytesIO()
            crop.resize((width, height), Image.Resampling.LANCZOS).save(out, format="PNG")
        png = out.getvalue()
        return self._image_result(png, f"zoom {list(region)} -> {width}x{height}", tab.page.url)

    # pointer --------------------------------------------------------------

    def _click(self, member: str, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        target = self._target(action_input)
        mods = self._modifiers(action_input.get("modifiers"))
        button: Literal["left", "middle", "right"] = _CLICK_BUTTONS.get(member, "left")
        count = {"double_click": 2, "triple_click": 3}.get(member, 1)
        verb = _CLICK_VERBS[member]
        suffix = f" with {'+'.join(mods)}" if mods else ""
        if target["type"] == "ref":
            ref = str(target["ref"])
            loc = self._live_locator(tab, ref)
            info = self._info(tab, ref)
            try:
                loc.scroll_into_view_if_needed()
                loc.click(button=button, click_count=count, modifiers=mods)
            except PlaywrightError as exc:
                if loc.count() == 0 or "detached" in str(exc).lower():
                    raise _StaleRef(ref) from exc
                raise _Refused(f"Error: could not click {ref}: {_first_line(exc)}") from exc
            self._settle(tab.page)
            return self._ok(f"{verb} {self._label(info)}{suffix}")
        x, y = self._coord(target)
        for mod in mods:
            tab.page.keyboard.down(mod)
        try:
            tab.page.mouse.click(x, y, button=button, click_count=count)
        finally:
            for mod in reversed(mods):
                tab.page.keyboard.up(mod)
        self._settle(tab.page)
        return self._ok(f"{verb} at ({int(target['x'])}, {int(target['y'])}){suffix}")

    def _m_left_click(self, action_input: dict[str, Any]) -> ActionResult:
        return self._click("left_click", action_input)

    def _m_right_click(self, action_input: dict[str, Any]) -> ActionResult:
        return self._click("right_click", action_input)

    def _m_middle_click(self, action_input: dict[str, Any]) -> ActionResult:
        return self._click("middle_click", action_input)

    def _m_double_click(self, action_input: dict[str, Any]) -> ActionResult:
        return self._click("double_click", action_input)

    def _m_triple_click(self, action_input: dict[str, Any]) -> ActionResult:
        return self._click("triple_click", action_input)

    def _m_hover(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        target = self._target(action_input)
        if target["type"] == "ref":
            ref = str(target["ref"])
            loc = self._live_locator(tab, ref)
            info = self._info(tab, ref)
            try:
                loc.hover()
            except PlaywrightError as exc:
                if loc.count() == 0:
                    raise _StaleRef(ref) from exc
                raise _Refused(f"Error: could not hover {ref}: {_first_line(exc)}") from exc
            return self._ok(f"Hovered {self._label(info)}")
        x, y = self._coord(target)
        tab.page.mouse.move(x, y)
        return self._ok(f"Hovered at ({int(target['x'])}, {int(target['y'])})")

    def _m_left_click_drag(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        start = self._target(action_input, "from")
        end = self._target(action_input, "target")
        if start["type"] != "coordinate" or end["type"] != "coordinate":
            raise _Refused("Error: left_click_drag takes coordinate targets for from and target.")
        x0, y0 = self._coord(start)
        x1, y1 = self._coord(end)
        mouse = tab.page.mouse
        mouse.move(x0, y0)
        mouse.down()
        mouse.move(x1, y1, steps=12)
        mouse.up()
        return self._ok(
            f"Dragged from ({int(start['x'])}, {int(start['y'])}) "
            f"to ({int(end['x'])}, {int(end['y'])})"
        )

    def _mouse_at(self, action_input: dict[str, Any]) -> tuple[_Tab, dict[str, Any]]:
        tab = self._tab_for(action_input)
        target = self._target(action_input)
        if target["type"] != "coordinate":
            raise _Refused("Error: this member takes a coordinate target.")
        x, y = self._coord(target)
        tab.page.mouse.move(x, y)
        return tab, target

    def _m_left_mouse_down(self, action_input: dict[str, Any]) -> ActionResult:
        tab, target = self._mouse_at(action_input)
        tab.page.mouse.down()
        return self._ok(f"Mouse down at ({int(target['x'])}, {int(target['y'])})")

    def _m_left_mouse_up(self, action_input: dict[str, Any]) -> ActionResult:
        tab, target = self._mouse_at(action_input)
        tab.page.mouse.up()
        return self._ok(f"Mouse up at ({int(target['x'])}, {int(target['y'])})")

    def _m_mouse_move(self, action_input: dict[str, Any]) -> ActionResult:
        _tab, target = self._mouse_at(action_input)
        return self._ok(f"Moved mouse to ({int(target['x'])}, {int(target['y'])})")

    def _m_scroll(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        direction = str(action_input.get("scroll_direction") or "down").lower()
        if direction not in ("up", "down", "left", "right"):
            raise _Refused("Error: scroll_direction must be up, down, left or right.")
        amount = action_input.get("scroll_amount", 3)
        try:
            units = max(1, min(10, int(amount)))
        except (TypeError, ValueError) as exc:
            raise _Refused("Error: scroll_amount must be a number 1-10.") from exc
        target = action_input.get("target")
        where = ""
        if isinstance(target, dict) and target.get("type") == "ref":
            ref = str(target.get("ref"))
            loc = self._live_locator(tab, ref)
            loc.hover()
            where = f" over {self._label(self._info(tab, ref))}"
        elif isinstance(target, dict) and target.get("type") == "coordinate":
            x, y = self._coord(self._target(action_input))
            tab.page.mouse.move(x, y)
            where = f" at ({int(target['x'])}, {int(target['y'])})"
        else:
            tab.page.mouse.move(self._viewport[0] / 2, self._viewport[1] / 2)
        px = units * SCROLL_PX_PER_UNIT
        dx, dy = {
            "up": (0, -px),
            "down": (0, px),
            "left": (-px, 0),
            "right": (px, 0),
        }[direction]
        tab.page.mouse.wheel(dx, dy)
        tab.page.wait_for_timeout(100)
        return self._ok(f"Scrolled {direction} {units}{where}")

    def _m_scroll_to(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        target = self._target(action_input)
        if target["type"] != "ref":
            raise _Refused("Error: scroll_to takes a ref target.")
        ref = str(target["ref"])
        loc = self._live_locator(tab, ref)
        info = self._info(tab, ref)
        try:
            loc.scroll_into_view_if_needed()
        except PlaywrightError as exc:
            if loc.count() == 0:
                raise _StaleRef(ref) from exc
            raise _Refused(f"Error: could not scroll to {ref}: {_first_line(exc)}") from exc
        return self._ok(f"Scrolled to {self._label(info)}")

    # keyboard & timing ----------------------------------------------------

    def _m_type(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        text = action_input.get("text")
        if not isinstance(text, str):
            raise _Refused("Error: type needs text.")
        target = action_input.get("target")
        focused = ""
        if isinstance(target, dict) and target.get("type") == "ref":
            ref = str(target.get("ref"))
            self._live_locator(tab, ref).click()
            focused = f" into {self._label(self._info(tab, ref))}"
        tab.page.keyboard.type(text)
        shown = text if len(text) <= 40 else text[:39] + "…"
        return self._ok(f"Typed {shown!r}{focused}", summary=f"typed {len(text)} chars")

    def _m_key(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        text = action_input.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _Refused(
                "Error: key needs text such as 'Enter', 'ctrl+a' or 'Backspace Backspace'."
            )
        repeat = action_input.get("repeat", 1)
        try:
            times = max(1, min(100, int(repeat)))
        except (TypeError, ValueError) as exc:
            raise _Refused("Error: repeat must be a number 1-100.") from exc
        chords = [self._key_chord(tok) for tok in text.split()]
        for _ in range(times):
            for chord in chords:
                tab.page.keyboard.press(chord)
        self._settle(tab.page)
        pressed = " ".join(chords)
        suffix = f" x{times}" if times > 1 else ""
        return self._ok(f"Pressed {pressed}{suffix}")

    def _m_hold_key(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        text = action_input.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _Refused("Error: hold_key needs text.")
        seconds = self._duration(action_input.get("duration"), default=1.0)
        keys = self._key_chord(text.strip()).split("+")
        keys = [k if k else "+" for k in keys]
        for key in keys:
            tab.page.keyboard.down(key)
        try:
            time.sleep(seconds)
        finally:
            for key in reversed(keys):
                tab.page.keyboard.up(key)
        return self._ok(f"Held {'+'.join(keys)} for {seconds:g}s")

    def _m_wait(self, action_input: dict[str, Any]) -> ActionResult:
        seconds = self._duration(action_input.get("duration"), default=1.0)
        time.sleep(seconds)
        return self._ok(f"Waited {seconds:g}s")

    # page reading ---------------------------------------------------------

    def _m_read_page(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        filt = action_input.get("filter")
        if filt not in (None, "", "interactive", "all"):
            raise _Refused("Error: filter must be 'interactive', 'all' or omitted.")
        depth = action_input.get("depth", 15)
        try:
            depth = max(1, min(100, int(depth)))
        except (TypeError, ValueError) as exc:
            raise _Refused("Error: depth must be a number.") from exc
        ref = action_input.get("ref")
        if ref is not None and not _REF_RE.match(str(ref)):
            raise _StaleRef(str(ref))
        data = self._snapshot(
            tab, {"filter": filt or "visible", "depth": depth, "ref": ref or None}
        )
        text = str(data.get("text") or "")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n… (truncated: page tree exceeds 50,000 characters)"
        if not text:
            text = "(no elements matched)"
        count = int(data.get("count") or 0)
        return self._ok(text, summary=f"read {count} elements ({filt or 'visible'})")

    def _m_find(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        query = action_input.get("query")
        if not isinstance(query, str) or not query.strip():
            raise _Refused("Error: find needs a query.")
        data = self._snapshot(tab, {"filter": "interactive", "depth": 100, "context": True})
        nodes = [n for n in (data.get("nodes") or []) if isinstance(n, dict)]
        ranked = self._rank(query, nodes)[:FIND_LIMIT]
        if not ranked:
            return self._ok(
                f"No elements matched {query!r}. Try read_page for the full tree.",
                summary=f"find {query!r}: 0 matches",
            )
        text = "\n".join(str(n.get("line") or "") for n in ranked)
        return self._ok(text, summary=f"find {query!r}: {len(ranked)} matches")

    @staticmethod
    def _rank(query: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        q = " ".join(query.lower().split())
        words = [w for w in re.findall(r"[a-z0-9]+", q) if len(w) > 1 or w.isdigit()]
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for order, node in enumerate(nodes):
            name = " ".join(str(node.get("name") or "").lower().split())
            role = str(node.get("role") or "").lower()
            context = " ".join(str(node.get("context") or "").lower().split())
            name_words = set(re.findall(r"[a-z0-9]+", name))
            context_words = set(re.findall(r"[a-z0-9]+", context))
            score = 0
            if q and q in name:
                score += 10
            if q and q in context:
                score += 3
            for w in words:
                if w == role:
                    score += 2
                if w in name_words:
                    score += 3
                elif w in name:
                    score += 2
                elif w in context_words:
                    score += 1
            if score > 0:
                scored.append((score, order, node))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [node for _score, _order, node in scored]

    def _m_get_page_text(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        text = tab.page.evaluate(PAGE_TEXT_JS)
        text = str(text or "")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n… (truncated at 50,000 characters)"
        if not text:
            text = "(the page has no text)"
        return self._ok(text, summary=f"page text, {len(text)} chars")

    # forms ----------------------------------------------------------------

    def _m_form_input(self, action_input: dict[str, Any]) -> ActionResult:
        tab = self._tab_for(action_input)
        target = self._target(action_input)
        if target["type"] != "ref":
            raise _Refused("Error: form_input takes a ref target.")
        if "value" not in action_input:
            raise _Refused("Error: form_input needs a value.")
        value = action_input["value"]
        ref = str(target["ref"])
        loc = self._live_locator(tab, ref)
        info = self._info(tab, ref)
        kind = loc.evaluate(CONTROL_KIND_JS)
        if not isinstance(kind, dict):
            raise _StaleRef(ref)
        tag = str(kind.get("tag") or "")
        itype = str(kind.get("type") or "")
        try:
            if tag == "input" and itype in ("checkbox", "radio"):
                wanted = self._truthy(value)
                loc.set_checked(wanted)
                shown = "checked" if wanted else "unchecked"
                return self._ok(f"Set {self._label(info)} {shown}")
            if tag == "select":
                text = str(value)
                try:
                    loc.select_option(value=text)
                except PlaywrightError:
                    loc.select_option(label=text)
                return self._ok(f"Selected {text!r} in {self._label(info)}")
            if tag in ("input", "textarea") or kind.get("editable"):
                if isinstance(value, bool):
                    text = "true" if value else "false"
                else:
                    text = str(value)
                loc.fill(text)
                return self._ok(f"Set {self._label(info)} to {text!r}")
            role = str(kind.get("role") or "")
            if role in ("checkbox", "switch", "menuitemcheckbox", "radio"):
                wanted = self._truthy(value)
                current = kind.get("ariaChecked") == "true"
                if current != wanted:
                    loc.click()
                shown = "checked" if wanted else "unchecked"
                return self._ok(f"Set {self._label(info)} {shown}")
        except PlaywrightError as exc:
            if loc.count() == 0:
                raise _StaleRef(ref) from exc
            raise _Refused(f"Error: could not set {ref}: {_first_line(exc)}") from exc
        raise _Refused(
            f"Error: {ref} is a <{tag}>, not a form control. Use left_click or type instead."
        )

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return value != 0
        return str(value).strip().lower() in ("true", "1", "yes", "on", "checked")

    # tabs -----------------------------------------------------------------

    def _m_new_tab(self, _action_input: dict[str, Any]) -> ActionResult:
        page = self._context.new_page()
        tab = self._find_tab_by_page(page) or self._adopt(page, announce=True)
        self._active = tab
        return self._state_only()

    def _find_tab_by_page(self, page: Page) -> _Tab | None:
        for tab in self._tabs:
            if tab.page is page:
                return tab
        return None

    def _m_list_tabs(self, _action_input: dict[str, Any]) -> ActionResult:
        return self._state_only()

    def _m_switch_tab(self, action_input: dict[str, Any]) -> ActionResult:
        tab_id = action_input.get("tab_id")
        tab = self._find_tab(tab_id)
        if tab is None or tab.page.is_closed():
            raise _Refused(f"Error: unknown tab_id {tab_id!r}. Use list_tabs to see open tabs.")
        self._active = tab
        try:
            tab.page.bring_to_front()
        except PlaywrightError:
            pass
        return self._state_only()

    def _m_close_tab(self, action_input: dict[str, Any]) -> ActionResult:
        tab_id = action_input.get("tab_id")
        tab = self._find_tab(tab_id)
        if tab is None or tab.page.is_closed():
            raise _Refused(f"Error: unknown tab_id {tab_id!r}. Use list_tabs to see open tabs.")
        tab.page.close()
        self._on_closed(tab)
        self._active_tab()  # opens a blank tab if that was the last one
        return self._state_only()


__all__ = ["ACTION_TIMEOUT_MS", "DISABLED_MEMBERS", "PlaywrightExecutor"]
