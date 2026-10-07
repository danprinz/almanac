// One way to spell a button (D169: plain language and real buttons).
//
// `ha-button` is in Home Assistant's entry bundle, so it needs no loading.
// Attribute vocabulary verified against `src/components/ha-button.ts` at
// home-assistant/frontend tag 20260826.7: `variant` brand | neutral | success |
// warning | danger, `appearance` accent | filled | outlined | plain, `size`
// xs | s | m | l | xl (there is no "small"), `disabled`, and `start` / `end`
// slots for an icon. Height is `--ha-button-height`, not `min-height`.

import { html, nothing } from "lit";
import type { TemplateResult } from "lit";

export type ButtonKind = "primary" | "ghost" | "danger";

export interface ButtonOptions {
  kind?: ButtonKind;
  disabled?: boolean;
  /** A toggle's state; a pressed ghost button is drawn filled. */
  pressed?: boolean;
  /** An `mdi:` icon shown before the label. */
  icon?: string;
  label?: string;
  onClick: (event: Event) => void;
}

/** `kind` maps onto HA's own appearance and variant, never onto our own CSS. */
export const haButton = (
  options: ButtonOptions,
  content: unknown,
): TemplateResult => {
  const kind = options.kind ?? "ghost";
  const filled = kind !== "ghost" || options.pressed === true;
  return html`<ha-button
    size="s"
    appearance=${filled ? "filled" : "plain"}
    variant=${kind === "danger"
      ? "danger"
      : kind === "primary" || options.pressed === true
        ? "brand"
        : "neutral"}
    aria-label=${options.label ?? nothing}
    aria-pressed=${options.pressed === undefined
      ? nothing
      : String(options.pressed)}
    ?disabled=${options.disabled ?? false}
    @click=${options.onClick}
    >${options.icon === undefined
      ? nothing
      : html`<ha-icon slot="start" icon=${options.icon}></ha-icon>`}${content}</ha-button
  >`;
};

export const ICON_ADD = "mdi:plus";
export const ICON_REMOVE = "mdi:delete-outline";
