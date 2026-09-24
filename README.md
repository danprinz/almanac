# almanac

A scheduler for Home Assistant whose rule model is **enumerable** — so *"what will happen
between now and Friday night"* is a view you can actually render, not a question you answer by
reading automations.

> **Status: design complete, no code yet.** Nothing here is installable. The model is settled
> and recorded; implementation has not started.

## The problem

Home Assistant's scheduling story splits badly. Native automations are fully general and
therefore opaque — you cannot ask them what will happen on Friday. The `scheduler-component`
stack is legible but hemmed in: sun-only time anchors, one service call per timeslot,
conditions that hide inside attributes so a gated schedule still displays as "daily", and
completion modes whose names don't match what they do.

A schedule you can read is a schedule you can't express, and a schedule you can express is one
you can't read.

## What's different

- **Rules have two shapes** — a *moment* or an *interval* — and the 24-hour bar is a rendering
  rather than the model. Intervals are idempotent, which is what makes restart recovery and
  desired-state actions well-defined instead of heuristic.
- **Any timestamp entity can anchor a rule**, ± an offset. *"45 minutes before candle
  lighting"* is expressible. This was explicitly refused upstream, which is why this exists.
- **One resolver contract for every non-clock time source.** Sun is the first implementation,
  not a special case; the Jewish calendar is the second. Adding a time source means adding an
  implementation, not patching the engine.
- **A timeline.** Every schedule resolved across a window, showing what fires when and which
  conditions gate it — plus a dry run at a hypothetical time, and a past/future view with *now*
  as the divider.
- **Enumerability is enforced by a type signature, not a policy.** A time source that cannot
  forecast a window cannot implement the contract, so it cannot exist. That constraint is what
  buys the timeline.

## Design intent

This is intended as a public Home Assistant custom integration, distributed through HACS as a
**single installation** — integration and frontend in one package, not two repositories that
have to be kept in step.

## Documents

| File | Contains |
| --- | --- |
| [`PRODUCT_BRIEF.md`](PRODUCT_BRIEF.md) | requirements — keep / fix / add, non-goals, and the five framing questions with their answers |
| [`DESIGN.md`](DESIGN.md) | the decisions with their reasoning, numbered and citable, plus the build order and an appendix of facts verified against upstream source |
| [`CLAUDE.md`](CLAUDE.md) | orientation and working rules for anyone — human or agent — picking this up |

`DESIGN.md` is the one to read. Every decision carries why it was made, and several record a
correction where checking the source changed the answer.

## Relationship to the existing scheduler

This is not a fork of [`nielsfaber/scheduler-component`](https://github.com/nielsfaber/scheduler-component)
and shares no code with it. Compatibility with the existing card is not a goal; a best-effort
importer for existing schedules is planned, and is deliberately the last thing built so that the
old storage format cannot shape the new model.

Credit where it's due: that project defined what a legible Home Assistant scheduler looks like,
and the problems catalogued above were found by using it for years rather than by reading it.

## Licence

[MIT](LICENSE) — use it, fork it, ship it commercially. The only condition is that the copyright
notice travels with it, which is how attribution back to this project is preserved. No warranty
and no liability.
