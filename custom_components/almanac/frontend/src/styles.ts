// Shared styles, written against Home Assistant's own CSS custom properties.
//
// No colour is spelled as a hex value anywhere in this project's frontend. Every
// one resolves through a `--primary-text-color`-style token, which is what makes
// the surfaces follow the user's theme — including the dark themes, where a
// hard-coded "subtle grey" becomes invisible rather than subtle.
//
// `ux/FINDINGS.md` finding 17 is the one rule here that is not inherited:
// **amber means exactly one thing** — "almanac does not know". It is not also
// used for "disabled", which is `--secondary-text-color` and a hollow mark, nor
// for "failed", which is `--error-color`. A colour that means two things means
// neither.

import { css } from "lit";

export const almanacTokens = css`
  :host {
    /* The three states a mark can be in, and nothing else may use them. */
    --almanac-known: var(--primary-color);
    --almanac-unknown: var(--warning-color, #ffa600);
    --almanac-failed: var(--error-color, #db4437);
    --almanac-disarmed: var(--secondary-text-color);

    --almanac-rail: var(--divider-color);
    --almanac-now: var(--accent-color, var(--primary-color));

    --almanac-gap-xs: 4px;
    --almanac-gap-sm: 8px;
    --almanac-gap-md: 16px;
    --almanac-gap-lg: 24px;
  }
`;

export const almanacText = css`
  .muted {
    color: var(--secondary-text-color);
  }

  .mono {
    font-family: var(--ha-font-family-code, ui-monospace, monospace);
    font-variant-numeric: tabular-nums;
  }

  .problem {
    color: var(--almanac-unknown);
  }

  /* Never a bare colour swatch: finding 17 wants one meaning per colour, and a
     colour alone is also unreadable to anyone who cannot tell two of them
     apart. Every state carries a word or a shape as well. */
  .pill {
    display: inline-flex;
    align-items: center;
    gap: var(--almanac-gap-xs);
    padding: 2px var(--almanac-gap-sm);
    border-radius: 12px;
    font-size: 0.8125rem;
    line-height: 1.4;
    white-space: nowrap;
    border: 1px solid var(--almanac-rail);
  }

  .pill.unknown {
    border-color: var(--almanac-unknown);
    color: var(--almanac-unknown);
  }

  .pill.disarmed {
    border-style: dashed;
    color: var(--almanac-disarmed);
  }

  .pill.failed {
    border-color: var(--almanac-failed);
    color: var(--almanac-failed);
  }
`;

/**
 * The form vocabulary, shared by the editor and the four builders under it.
 *
 * It moved out of `editor.ts` at step 9e for the ordinary reason — five
 * components now draw the same field, and five copies of the same rule drift —
 * but also because the rules here are decisions, not taste. The visible label,
 * the 40px control, the one-meaning-per-colour button classes: each is written
 * down once so that a builder cannot quietly disagree with the editor it is
 * nested inside.
 *
 * `--almanac-*` tokens are used throughout, so this is only usable alongside
 * `almanacTokens`.
 */
export const almanacForm = css`
  h3 {
    font-size: 0.8125rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin: 0 0 var(--almanac-gap-sm);
    color: var(--secondary-text-color);
  }

  h4 {
    font-size: 0.8125rem;
    margin: 0;
    color: var(--secondary-text-color);
  }

  section {
    display: flex;
    flex-direction: column;
    gap: var(--almanac-gap-sm);
  }

  /* A nested surface is the one thing that is not the page, so it gets an edge.
     Everything else sits flat on the card. */
  .panel {
    border: 1px solid var(--almanac-rail);
    border-radius: 12px;
    padding: var(--almanac-gap-md);
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--almanac-gap-xs);
  }

  .field {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--almanac-gap-sm);
  }

  /* Pushes whatever follows it to the end of a .row — a remove button, the
     arrows that reorder an action — without making the row a grid. */
  .spacer {
    flex: 1 1 auto;
  }

  /* Visible, always, and never a placeholder standing in for one: a label that
     vanishes once the field has content takes the field's meaning with it, and
     these fields hold slugs and signed numbers. */
  .field > span:first-child {
    flex: 0 0 7rem;
    font-size: 0.875rem;
    color: var(--secondary-text-color);
  }

  .field input,
  .field select,
  .field textarea {
    flex: 1 1 12rem;
    min-width: 0;
    min-height: 40px;
    box-sizing: border-box;
    padding: 0 var(--almanac-gap-sm);
    font: inherit;
    color: var(--primary-text-color);
    background: var(--secondary-background-color);
    border: 1px solid var(--almanac-rail);
    border-radius: 8px;
  }

  .field input:disabled,
  .field select:disabled,
  .field textarea:disabled {
    opacity: 0.6;
  }

  .field input[type="number"] {
    flex: 0 0 6rem;
  }

  /* A payload is JSON, so it is monospaced and it wraps. The zero padding from
     the rule above would put the first line against the border. */
  .field textarea {
    flex: 1 1 100%;
    padding: var(--almanac-gap-sm);
    min-height: 88px;
    resize: vertical;
    font-family: var(--ha-font-family-code, ui-monospace, monospace);
    font-size: 0.8125rem;
    line-height: 1.5;
  }

  .field textarea.bad {
    border-color: var(--almanac-failed);
  }

  .check {
    display: flex;
    align-items: center;
    gap: var(--almanac-gap-sm);
    font-size: 0.875rem;
  }

  button {
    font: inherit;
    color: inherit;
    border-radius: 10px;
    border: 1px solid var(--almanac-rail);
    background: none;
    min-height: 40px;
    padding: 0 var(--almanac-gap-sm);
  }

  button:disabled {
    opacity: 0.5;
  }

  .chip {
    padding: 0 var(--almanac-gap-sm);
    min-height: 36px;
  }

  .chip.selected {
    border-color: var(--almanac-known);
    color: var(--almanac-known);
  }

  .primary {
    border-color: var(--almanac-known);
    color: var(--almanac-known);
  }

  .danger {
    border-color: var(--almanac-failed);
    color: var(--almanac-failed);
  }

  p {
    margin: 0;
    font-size: 0.875rem;
  }

  /* The label gives up its column before the control gives up its width. A
     7rem label beside a 12rem field needs 19rem, and a phone in portrait does
     not have it. */
  @media (max-width: 600px) {
    .field > span:first-child {
      flex: 1 1 100%;
    }
  }
`;

/**
 * A 44×44 hit target wherever something is tappable. The visible mark is often
 * much smaller than that — a stage dot on a micro-track is 8px — so the target
 * is grown with padding rather than by growing the mark.
 */
export const almanacHitTarget = css`
  .tappable {
    position: relative;
    cursor: pointer;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    padding: 0;
  }

  .tappable::after {
    content: "";
    position: absolute;
    inset: 50% auto auto 50%;
    width: 44px;
    height: 44px;
    transform: translate(-50%, -50%);
  }

  .tappable:focus-visible {
    outline: 2px solid var(--almanac-now);
    outline-offset: 2px;
  }
`;
