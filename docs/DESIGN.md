# CRYPTUS design system — "The Vault"

*Cryptus* is Latin for a hidden, vaulted chamber (the root of *crypt* and *cryptography*). The identity
treats the product as a precision vault: dark graphite chambers, a single crimson seal, and one
recognisable cut.

## Signature elements

| Element | Rule | Why |
|---|---|---|
| **The cut** | Major surfaces chamfer their top-left and bottom-right corners at 45° (`--cut-lg/md/sm`). Buttons chamfer one corner | Recognisable without the logo, and nods to vault bolts and cut stone |
| **Crimson as a seal, not a flood** | Crimson marks state: the active nav rail, focus, the primary action, a seam under headings, a gradient edge on featured panels | Keeps a dark UI calm. Red keeps its meaning |
| **Engraved labels** | 11 px uppercase Archivo at 125 % width, 0.14 em tracking | Reads as stamped metal and gives a strong hierarchy |
| **Mono figures** | Every amount, address, hash and count uses JetBrains Mono with tabular numerals | Financial data aligns and never jitters |
| **Diamond glyph** | Status badges lead with a 45° square. Empty states and wizard steps use rotated frames | Echoes the logo's core |
| **Vault floor** | A faint 48 px engraved grid fades from the top-left of every view | Gives depth without decoration |

## Logo

An octagonal vault door drawn as an open "C" (the gap faces right), with a crimson diamond core and a bolt
sliding out through the opening. Files: `frontend/public/favicon.svg`, `frontend/src/components/Logo.tsx`.
The wordmark is Archivo 720 at 125 % width with 0.2 em tracking.

## Tokens

All values live in `frontend/src/styles/tokens.css`. Components use semantic tokens only.

* **Color.** `--bg #09090B`, `--surface #121216`, `--surface-raised #19191F`, `--accent #E51D3D`,
  `--accent-strong #C8102E` (fills carrying white text, AA), `--accent-deep #A5122C`, `--fg #F4F4F5`,
  `--fg-muted #92929B`, `--success #20C997`, `--warning #E8B04A`, `--danger #E5484D`.
  Danger is always paired with an icon or label, because it sits close to the brand red.
* **Themes.** "Vault" (dark, default) and "Ledger" (light, warm paper) redefine the same semantic tokens.
* **Type.** Archivo Variable (width axis 62–125 %) and JetBrains Mono Variable, both self-hosted.
  Scale: 11 / 12 / 13 / 14 / 16 / 20 / 26 / 34 / 44 px.
* **Space.** 4 px grid: 2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64.
* **Motion.** 120 / 200 / 420 ms on `cubic-bezier(.2,.8,.2,1)`. Views rise 4 px into place, seams draw
  left to right, and confirmations fill notch by notch. All motion collapses under `prefers-reduced-motion`.
* **Breakpoints.** 640 (phone), 900 (sidebar becomes a drawer), 1100/1200 (dense grids).

## Components

`frontend/src/components/ui.tsx` defines Button, Input, Select, Textarea, Switch, Check, Panel, Alert,
Status, Kv, CopyField, Segmented, Tabs, Dialog (focus-trapped), Toasts, Confirmations, Empty and
PageHead. `BarChart.tsx` is a single-series chart with keyboard-focusable bars, a tooltip and a table
view. Every state (hover, focus-visible, active, disabled, loading, invalid) comes from the same
tokens.

## Accessibility

* Text contrast meets WCAG AA on all surfaces. White on `--accent-strong` is 5.9:1.
* Visible 2 px focus rings, a focus trap and Escape handling in dialogs, labelled controls, and
  `aria-live` on checkout progress and toasts.
* Status is never conveyed by color alone (label + glyph).
