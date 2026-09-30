"""D51 — describe almanac's occurrence event to the logbook.

Shipping this file is not decoration, and the reason is A.4: the logbook's
causation chain is *generic*. `ContextAugmenter.augment` in
`components/logbook/processor.py` will only reach `CONTEXT_NAME` /
`CONTEXT_MESSAGE` / `CONTEXT_DOMAIN` for a context row whose event type is in
`external_events`, and `external_events` is populated from exactly one place:
`_process_logbook_platform` calling `async_describe_events` on each integration
that ships a module of this name. Automations get "triggered by" for that reason
and no other. So this file is what turns D50's shared `Context` into the sentence
D50 is written to produce — without it the event is recorded, the state change is
recorded, and nothing joins them for a reader.

No manifest change goes with it. `async_process_integration_platforms` finds this
module by name on the loaded integration, so `logbook` is neither a dependency nor
an `after_dependency` — verified against 2026.9.4's own `automation/manifest.json`,
which declares neither and ships `automation/logbook.py` regardless.

**Only `almanac_occurrence` is described, deliberately.** `almanac_execution`
carries the per-action results and is left undescribed, so `_humanify` reaches its
`else: continue` and the event is recorded and queryable without putting a second
narrative row in the logbook for every occurrence. `events.py` argues that split.

**Nothing here reads a clock (D64)** and nothing here reads state. A describe
callback runs once per row of a history query, over rows that may be weeks old;
anything it looked up would be the answer for *now* and wrong for the row.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_CONTEXT_ID,
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
    LazyEventPartialState,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_NAME
from homeassistant.core import HomeAssistant, callback

from .const import (
    ATTR_BLOCKING,
    ATTR_CAUSE,
    ATTR_KIND,
    ATTR_LATENESS,
    ATTR_SCHEDULE_ID,
    DOMAIN,
    EVENT_OCCURRENCE,
)
from .engine.transition import ExitCause, TransitionKind

# One sentence per `TransitionKind`, written as the predicate of the schedule's
# name so the logbook reads "Shabbat lights — started". `RESUME` says why it is
# not `ENTER`: D90 exists because a restart mid-interval re-applies state without
# re-announcing anything, and a log line that called that "started" would have a
# user hunting for a schedule change that never happened.
_MESSAGES: dict[str, str] = {
    TransitionKind.FIRE: "fired",
    TransitionKind.ENTER: "started",
    TransitionKind.RESUME: "resumed after a restart",
    TransitionKind.EXIT: "ended",
    TransitionKind.SKIPPED: "was skipped",
    TransitionKind.MISSED: "was missed",
}

# D3's exit paths are three-valued and the causes are four; every one of them is a
# different thing to have to explain to somebody whose lights just changed, which
# is the sentence `ExitCause`'s own docstring uses. `WINDOW_END` is absent because
# it is the unremarkable case and "ended" already says it.
_CAUSES: dict[str, str] = {
    ExitCause.DISARMED: "ended because the schedule was turned off",
    ExitCause.GONE: "ended because its rule no longer exists",
    ExitCause.CONDITIONS: "ended because its conditions stopped holding",
}

# Below this, lateness is the ordinary cost of a timer firing and saying so would
# make every line noisier without making any of them more informative. Above it,
# "the 17:00 occurrence, handled at 17:04" is the thing a reader needs, and D49
# records both instants precisely so this line can be written.
_LATE_SECONDS = 1.0


@callback
def _message(data: dict[str, Any]) -> str:
    """The sentence for one occurrence, built only from its own payload."""
    kind = str(data.get(ATTR_KIND) or "")
    message = _MESSAGES.get(kind, kind or "did something unrecognised")

    if kind == TransitionKind.EXIT:
        message = _CAUSES.get(str(data.get(ATTR_CAUSE) or ""), message)

    # D24 — the label the user wrote, verbatim. This is the whole reason the
    # payload carries labels rather than a boolean: "skipped: cleaning crew
    # present" is actionable and "skipped" is not.
    if blocking := data.get(ATTR_BLOCKING):
        message = f"{message}: {', '.join(str(label) for label in blocking)}"

    lateness = float(data.get(ATTR_LATENESS) or 0.0)
    if lateness >= _LATE_SECONDS:
        message = f"{message} ({round(lateness)}s late)"
    return message


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[
        [str, str, Callable[[LazyEventPartialState], dict[str, Any]]], None
    ],
) -> None:
    """Register almanac's occurrence event with the logbook.

    The signature is core's, verified against 2026.9.4's
    `components/logbook/__init__.py::_process_logbook_platform`, which calls
    `platform.async_describe_events(hass, _async_describe_event)`.
    """

    @callback
    def async_describe_occurrence(event: LazyEventPartialState) -> dict[str, Any]:
        """Describe one `almanac_occurrence` row."""
        data = event.data
        return {
            # Falls back to the schedule id rather than to nothing: a row with no
            # name renders as a blank line, and an opaque id at least identifies
            # which schedule to go and look at.
            LOGBOOK_ENTRY_NAME: data.get(ATTR_NAME) or data.get(ATTR_SCHEDULE_ID),
            LOGBOOK_ENTRY_MESSAGE: _message(data),
            # D54's switch, so the row lands on the schedule's own page.
            LOGBOOK_ENTRY_ENTITY_ID: data.get(ATTR_ENTITY_ID),
            LOGBOOK_ENTRY_CONTEXT_ID: event.context_id,
        }

    # `LOGBOOK_ENTRY_SOURCE` is deliberately not set. `augment` drops
    # `CONTEXT_MESSAGE` in favour of `CONTEXT_SOURCE` when both are present, and
    # the message is the informative half here — a light caused by this schedule
    # should read "triggered by Shabbat lights — started", not "triggered by"
    # followed by a restatement of the name.
    async_describe_event(DOMAIN, EVENT_OCCURRENCE, async_describe_occurrence)
