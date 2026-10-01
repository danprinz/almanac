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
