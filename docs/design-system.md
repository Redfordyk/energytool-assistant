# Energytool design system

The visual system is shared by the customer chat and the knowledge-base admin page. The live tokens are in `app/static/tokens.css`; page-specific component rules live in `index.css` and `admin.css`.

## Foundations

| Group | Token | Value | Use |
| --- | --- | --- | --- |
| Brand | `--color-brand` | `#e30611` | Primary actions, emphasis, links |
| Brand | `--color-brand-hover` | `#c4040e` | Hover state for primary actions |
| Brand | `--color-brand-soft` | `#fff2f3` | Quiet brand surfaces |
| Text | `--color-text` | `#18202f` | Main text |
| Text | `--color-text-secondary` | `#535d6d` | Supporting text |
| Text | `--color-text-muted` | `#768093` | Hints and metadata |
| Surface | `--color-canvas` | `#f6f7fa` | Page background |
| Surface | `--color-surface` | `#fff` | Cards and controls |
| Surface | `--color-surface-subtle` | `#f8f9fb` | Secondary panels |
| Border | `--color-border` | `#e4e7ed` | Card and input boundaries |
| Status | `--color-success` / `--color-success-surface` | green | Available and complete states |
| Status | `--color-warning` / `--color-warning-surface` | amber | Review and caution states |
| Status | `--color-danger` / `--color-danger-surface` | red | Unavailable and error states |

Typography uses the system sans-serif stack with a monospace stack for hashes and diagnostic output. Spacing is based on a 4 px step (`--space-1` through `--space-8`). Components use the shared small, medium, large, extra-large, and pill radii. Cards share one low-contrast shadow.

## Components and behavior

- Primary buttons use the MTS red; secondary buttons use a white surface and neutral border. Buttons have visible hover, active, disabled, and keyboard-focus states.
- Cards use white surfaces, neutral borders, and consistent rounded corners. Brand color marks the current task without replacing status color.
- Inputs share height, border, focus ring, placeholder contrast, and type scale. Labels remain visible outside placeholders.
- Status, warning, and error messages are announced through `role="status"` or `aria-live` regions. Focus remains visible for keyboard users.
- The admin page follows the same typography, spacing, surfaces, buttons, and responsive breakpoints as the chat. Diagnostic hashes wrap on narrow screens.
- Reduced-motion preferences disable nonessential transitions and smooth scrolling.
- Breakpoints are content-led: 860 px collapses the two-column customer workspace; 560 px stacks controls and tightens spacing for phones.

## Maintenance

Change a shared foundation in `tokens.css` first. Keep component-specific layout in the page stylesheet. Avoid introducing one-off color or spacing values when an existing token expresses the same role. Verify the chat and admin pages at desktop and phone widths after visual changes.
