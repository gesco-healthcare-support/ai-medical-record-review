# Design system reference

The frontend's visual layer: the Evaluators design tokens and class families, the Tailwind and
shadcn/ui bridge, the primitives in `components/ui`, fonts, and the breakpoints the CSS uses.

Source of truth: `frontend/app/evaluators-ds.css`, `frontend/app/globals.css`,
`frontend/app/layout.tsx`, `frontend/components/ui/`, `frontend/components.json`,
`frontend/lib/utils.ts`, `frontend/postcss.config.mjs`.

## Stylesheets and fonts

| Item | Value | Code |
| --- | --- | --- |
| Load order | `globals.css`, then `evaluators-ds.css`, both imported by the root layout | `frontend/app/layout.tsx` |
| `globals.css` | Imports `tailwindcss`, `tw-animate-css` and `shadcn/tailwind.css`; declares the Tailwind theme bridge, the shadcn semantic slots, and a few helper classes | `frontend/app/globals.css` |
| `evaluators-ds.css` | Every design token and every `ev-`, `auth-`, `hd-`, `rc-`, `rce-`, `sum-`, `bnd-`, `bundle-`, `admin-` and `dupe-` class | `frontend/app/evaluators-ds.css` |
| PostCSS | `@tailwindcss/postcss` only | `frontend/postcss.config.mjs` |
| Tailwind | v4, configured in CSS (`@theme`); there is no `tailwind.config` file | `frontend/components.json` (`"config": ""`) |
| Body font | Inter via `next/font/google`, exposed as `--font-inter`, `display: swap`, subset `latin` | `frontend/app/layout.tsx` |
| Heading font | Poppins weights 600 and 700 via `next/font/google`, exposed as `--font-poppins` | `frontend/app/layout.tsx` |
| Mono font | `ui-monospace, "Cascadia Code", Consolas, monospace` | `globals.css` `--font-mono`, `evaluators-ds.css` `.ev-mono` |
| Icons | `lucide-react` | components throughout |
| Theme | Light only. `globals.css` declares a `dark` custom variant, but no dark palette exists and the toaster is pinned to `light`. `next-themes` is listed in `package.json` and imported nowhere | `frontend/components/ui/sonner.tsx` |

## Design tokens

All defined on `:root` in `frontend/app/evaluators-ds.css`.

### Colour

| Token | Value | Used for (examples) |
| --- | --- | --- |
| `--navy-900` | `#1B2543` | Gold button text; toast surface (through `--color-navy-900`) |
| `--navy-700` | `#2A3760` | Primary button hover |
| `--navy-600` | `#32416C` | Brand spine: top bar, primary buttons, active chips and tabs |
| `--navy-400` | `#5A6A99` | Top bar divider, muted navy text |
| `--navy-300` | `#8C98B8` | Outline button border, split handle hover |
| `--navy-100` | `#E2E6EF` | `.hd-dots.open` |
| `--navy-50` | `#F1F3F8` | Hover and selected-row backgrounds |
| `--gold-700` | `#997B33` | Eyebrow text, gold button hover |
| `--gold-600` | `#C2A14D` | Gold button (suggested merges) |
| `--gold-400` | `#DCC07E` | Gap strip borders |
| `--gold-100` | `#FAF4E6` | Bridged to Tailwind as `gold-100`; no design-system class uses it |
| `--blue-600` | `#1A43BC` | Link hover, info banner text |
| `--blue-500` | `#1F4ED8` | Links, focus, edited state |
| `--blue-100` | `#E5ECFD` | Info banner and "edited" chip background |
| `--gray-50` | `#F4F6F9` | Page background |
| `--gray-100` | `#E7EAF0` | Tracks, table row rules, neutral chips |
| `--gray-200` | `#D2D7E0` | Borders |
| `--gray-300` | `#A9B0BF` | Strong borders, dashed dropzones |
| `--gray-400` | `#828A9B` | Placeholder and disabled text |
| `--gray-500` | `#5E6678` | Muted text |
| `--gray-600` | `#434B5E` | Labels |
| `--gray-700` | `#2C3344` | Body text in cards |
| `--ink` | `#1A1F2E` | Primary text |
| `--success-500` | `#1F8A5B` | Success text and badges |
| `--success-100` | `#E3F3EC` | Success background |
| `--warning-500` | `#C2820E` | Attention, paused, review chips |
| `--warning-100` | `#FAF0D9` | Attention background |
| `--danger-500` | `#C23934` | Errors, destructive actions |
| `--danger-100` | `#FBE7E6` | Error background |

### Roles, shadows, shape, layout, type

| Token | Value |
| --- | --- |
| `--color-border` | `var(--gray-200)` |
| `--color-border-strong` | `var(--gray-300)` |
| `--color-border-focus` | `var(--blue-500)` |
| `--color-text-muted` | `var(--gray-500)` |
| `--color-text-on-navy` | `#DDE3F0` |
| `--shadow-focus` | `0 0 0 3px color-mix(in srgb, #1F4ED8 40%, transparent)` |
| `--shadow-md` | `0 10px 30px rgba(27, 37, 67, .12)` |
| `--shadow-lg` | `0 16px 40px rgba(27, 37, 67, .18)` |
| `--radius-sm` | `6px` |
| `--radius-md` | `8px` |
| `--radius-lg` | `12px` |
| `--radius-pill` | `999px` |
| `--gutter` | `clamp(20px, 3.5vw, 72px)`: the side padding of every page column |
| `--content-cap` | `2560px`: the maximum column width |
| `--font-heading` | `var(--font-poppins), "Poppins"` |
| `--font-body` | `var(--font-inter), "Inter"` |

`--split-left` is set inline by `SplitPane` on `.ev-split` (default `58%`).

## Tailwind and shadcn bridge

`frontend/app/globals.css` maps the tokens into Tailwind so utilities and the shadcn components use
the same palette.

### `@theme` (Tailwind utilities)

| Theme variable | Value |
| --- | --- |
| `--color-navy-50`, `-100`, `-300`, `-400`, `-600`, `-700`, `-900` | `var(--navy-N)` |
| `--color-gold-100`, `-400`, `-600`, `-700` | `var(--gold-N)` |
| `--color-blue-100`, `-500`, `-600` | `var(--blue-N)` |
| `--color-gray-50` to `--color-gray-700` | `var(--gray-N)` |
| `--color-ink` | `var(--ink)` |
| `--color-success`, `--color-success-soft` | `var(--success-500)`, `var(--success-100)` |
| `--color-warning`, `--color-warning-soft` | `var(--warning-500)`, `var(--warning-100)` |
| `--color-danger`, `--color-danger-soft` | `var(--danger-500)`, `var(--danger-100)` |
| `--color-on-navy` | `var(--color-text-on-navy)` |
| `--radius-sm`, `--radius-md`, `--radius-lg`, `--radius-xl` | `6px`, `8px`, `12px`, `16px` |
| `--font-sans` | `var(--font-inter), "Segoe UI", system-ui, sans-serif` |
| `--font-heading` | `var(--font-poppins), "Segoe UI", system-ui, sans-serif` |
| `--font-mono` | `ui-monospace, "Cascadia Code", Consolas, monospace` |

These give utilities such as `bg-navy-600`, `text-danger`, `text-on-navy`, `border-gray-300` and
`font-heading`.

`--radius-sm`, `--radius-md`, `--radius-lg`, `--font-heading` and (through the shadcn slots below)
`--color-border` are defined in both files. The radius and border values agree. Tailwind emits theme
variables inside its `theme` cascade layer, and `evaluators-ds.css` is not in a layer, so where the
two differ (`--font-heading`) the `evaluators-ds.css` value is the one `var()` resolves to.

### shadcn semantic slots (`:root` and `@theme inline`)

| Slot | Value |
| --- | --- |
| `--background` | `#ffffff` |
| `--foreground` | `var(--ink)` |
| `--card`, `--popover` | `#ffffff` |
| `--card-foreground`, `--popover-foreground` | `var(--ink)` |
| `--primary` | `var(--navy-600)` |
| `--primary-foreground` | `#ffffff` |
| `--secondary` | `var(--blue-500)` |
| `--secondary-foreground` | `#ffffff` |
| `--muted`, `--accent` | `var(--gray-50)` |
| `--muted-foreground` | `var(--gray-500)` |
| `--accent-foreground` | `var(--navy-600)` |
| `--destructive` | `var(--danger-500)` |
| `--destructive-foreground` | `#ffffff` |
| `--border`, `--input` | `var(--gray-200)` |
| `--ring` | `var(--blue-500)` |
| `--radius` | `0.5rem` |

`@theme inline` exposes each slot as `--color-<slot>` (for example `bg-primary`, `text-muted-foreground`,
`bg-destructive`). The base layer applies `border-border` and `outline-ring/50` to every element.

## Class families

Every class defined in the two stylesheets, grouped by the section of `evaluators-ds.css` that defines
it. State modifiers (for example `active`, `selected`) are listed with the family they modify.

| Family | Classes | Used by |
| --- | --- | --- |
| Base and banners | `hidden`, `muted`, `error-text`, `banner` (red), `banner-info` (blue), `notice-attention`, `notice-attention-list` | Workbench banners, error text everywhere |
| App shell | `ev-topbar`, `ev-crest-chip`, `ev-wordmark`, `ev-topbar-divider`, `ev-topbar-app`, `ev-topbar-nav`, `ev-brand-home`, `ev-backlink`, `ev-page-back` | `AppBar`, `Brand`, `BackLink`, route pages |
| Stepper | `ev-stepper`, `ev-step` (with `active`, `done`, `busy`), `ev-step-line`, `ev-step-circle`, `ev-step-label` | `Stepper` (rendered only by its test) |
| Buttons | `ev-btn`, `ev-btn-primary`, `ev-btn-outline`, `ev-btn-ghost`, `ev-btn-gold`, `ev-btn-del`, `ev-btn-sm`, `ev-btn-lg`, `ev-btn-block` | Every page |
| Form controls | `ev-inp` (with `invalid`), `ev-lbl`, `ev-cb`, `ev-check`, `ev-eyebrow` | Every form |
| Chips | `ev-chip`, `ev-chip-review` (amber), `ev-chip-edit` (blue), `ev-chip-neutral` (gray), `ev-chip-off` (red) | Summary cards, duplicate clusters |
| Segmented tabs | `ev-segtabs`, `ev-segtab` (with `active`) | `SegmentedTabs` |
| Split pane | `ev-split` (with `dragging`), `ev-split-pane`, `ev-split-left`, `ev-split-right`, `ev-split-handle` | `SplitPane` |
| Dialog helpers | `ev-dialog-wide`, `ev-dialog-row`, `ev-field-1`, `ev-field-2`, `ev-mono`, `ev-refpanel`, `ev-refpanel-head`, `ev-refpanel-body` | `CategoryDialog`, `PromptDialog`, bundle aside |
| Legacy dialog | `ev-dialog-backdrop`, `ev-dialog`, `ev-dialog-head`, `ev-dialog-sub`, `ev-dialog-x`, `ev-dialog-foot` | none (dialogs use `components/ui/dialog.tsx`) |
| Pager | `ev-pager` | `SummariesView` |
| Auth | `auth-main`, `auth-card`, `auth-head`, `auth-crest`, `auth-sub`, `auth-form`, `auth-field`, `auth-field-error`, `auth-remember`, `auth-alt`, `auth-alert`, `auth-checklist`, `auth-check` (with `met`, `unmet`, `failed`), `auth-check-icon`, `auth-linkbtn` (in `globals.css`), `flashes`, `fs-gen-msgs` | `AuthShell` and the auth forms |
| My documents | `hd-column`, `hd-header`, `hd-toolbar`, `hd-chips`, `hd-chip` (with `active`), `hd-search`, `hd-card` (with `dragging`), `hd-table`, `hd-sortbtn` (in `globals.css`), `hd-sortlabel`, `hd-w-patient`, `hd-w-pages`, `hd-w-uploaded`, `hd-w-found`, `hd-w-activity`, `hd-w-status`, `hd-w-menu`, `hd-open-cue`, `hd-norows`, `hd-doc`, `hd-name`, `hd-muted`, `hd-foot`, `hd-foot-nav`, `hd-menu-cell`, `hd-dots` (with `open`), `hd-menu`, `hd-menu-divider`, `hd-admin-actions` | `DocumentsTable`, `DocumentsView`, `AdminView`, bundle picker |
| Badges | `hd-badge`, `hd-dot`, `hd-badge-success`, `hd-badge-warning`, `hd-badge-info`, `hd-badge-neutral`, `hd-badge-danger` | `StatusPill`, `AdminView` (built as `hd-badge-${tone}`) |
| Empty state | `hd-empty`, `hd-empty-sub`, `hd-drop` (with `dragging`), `hd-drop-icon`, `hd-drop-title`, `hd-drop-sub`, `hd-steps`, `hd-step`, `hd-step-num`, `hd-step-title`, `hd-step-sub` | `EmptyState` |
| Centered panels | `panel`, `center-panel`, `bar`, `bar-fill` | `StartPanel`, `ProgressPanel` |
| Review & correct table | `rc-titletd`, `rc-titlebar`, `rc-title`, `rc-rowactions`, `split-page`, `rc-inp`, `rc-selwrap`, `rc-sel`, `doc-row` (with `selected`, `skipped`, `invalid`, `attention`, `title-row`), `gap-row`, `rc-attn-chip`, `rc-unid-chip`, `rc-empty-filter`, `col-num`, `col-page`, `col-category`, `col-date`, `col-check`, `col-sum`, `add-form`, `rc-save` (with `saved`, `dirty`, `error`) | `RowsTable`, `ReviewEditor`, the autosave chip |
| Review & correct, earlier layout | `rc-column`, `rc-filename`, `rc-header`, `rc-status`, `rc-actions`, `editor-split`, `table-wrap`, `viewer-wrap` | none |
| Workbench shell | `rce`, `rce-bar`, `rce-bar-main`, `rce-bar-actions`, `rce-back`, `rce-title`, `rce-name`, `rce-count`, `rce-progress` (with `paused`), `rce-stop`, `rce-progress-label`, `rce-progress-bar`, `rce-progress-pct`, `rce-body`, `rce-editor` (with `busy`), `rce-toolbar`, `rce-table`, `rce-viewer`, `rce-split`, `rce-splitcol`, `row-jump` | `ReviewPageClient`, `ReviewEditor`, `DuplicatesView`, `SummariesView` (`rce-back` has no user) |
| Report header | `rc-headerbar`, `rc-hb-fields`, `rc-hb-field`, `rc-hb-firm`, `rc-hb-actions` | `HeaderBar` |
| Summaries | `sum-column`, `sum-header`, `sum-countline`, `summary-list`, `summary-card` (with `excluded`, `editing`, `selected`), `summary-head`, `sum-heading`, `card-actions`, `exclude-toggle`, `meta`, `body`, `meta-jump`, `sum-meta-row`, `sum-category`, `sum-category-label`, `verify-issues`, `vi-type`, `vi-detail`, `sum-title`, `sum-date`, `sum-text`, `edit-actions`, `summary-empty`, `empty-title` | `SummariesView`, and `DuplicatesView` for the column and cards |
| Duplicates | `dupe-similarity`, `dupe-copies`, `dupe-copy` (with `primary`, `selected`), `dupe-copy-main`, `dupe-copy-title`, `dupe-copy-actions` | `DuplicatesView` |
| PDF pane | `pdf-pane`, `pdf-pane-head`, `pdf-pane-name`, `pdf-pane-page` (all in `globals.css`) | `PdfViewer` |
| Bundles | `bnd-main`, `bnd-column`, `bnd-head`, `bnd-heading`, `bnd-lead`, `bnd-selectcell`, `bnd-breadcrumb`, `bnd-crumb-name`, `bnd-crumb-sep`, `bnd-linkbtn`, `bnd-grid`, `bnd-card-head`, `bnd-empty`, `bnd-empty-title`, `bnd-result` (with `ok`, `err`), `bundle-card`, `bundle-fields`, `bundle-buttons` | `BundlePageClient` |
| Bundles, earlier layout | `bundle-actions-inner`, `bundle-lead`, `bundle-upload` | none |
| Admin | `admin-desc`, `admin-examples`, `admin-inactive` | `AdminView` |

Classes marked "none" in the last column have no reference in any `.tsx` file under `frontend/app` or
`frontend/components` at this tree, and neither do `ev-chip-off`, `flashes` and `fs-gen-msgs`.

### ID and attribute selectors

| Selector | Styles | Referenced by |
| --- | --- | --- |
| `#rowsTable` | The Review & correct table, sticky header, number inputs without spinners | `RowsTable` |
| `#pdfFrame`, `.pdf-pane #pdfFrame` | The pdf.js iframe | `PdfViewer` |
| `#step-summaries` | The Summaries section sizing | `SummariesView` |
| `#step-editor`, `#bundle-actions`, `#bundleActionsHint`, `#bundleResultMsg`, `body[data-bundle-slug]` | Earlier layouts | none |
| `.hd-table tbody tr[data-id]` | Clickable My documents rows | `DocumentsTable` |
| `.hd-table thead th[data-sort]` | Sortable header cursor | none (headers use `hd-sortbtn`) |
| `button.ev-step` (in `globals.css`) | Resets button chrome for stepper steps | `Stepper` |
| `.ev-split.dragging iframe` | Disables iframe pointer events while dragging | `SplitPane` |

## Breakpoints

The CSS declares no minimum supported width. These are every width condition it uses.

| Condition | Where | Effect |
| --- | --- | --- |
| `max-width: 720px` | `evaluators-ds.css` | `.hd-steps` (the empty-state explainer) becomes one column |
| `max-width: 900px` | `evaluators-ds.css` (split pane block) | `.ev-split` stacks vertically; both panes full width; the handle is hidden |
| `max-width: 900px` | `evaluators-ds.css` (workbench block) | `.rce-table` is `60vh` tall, `.rce-viewer` `70vh`; `.rce-split` scrolls as one page |
| `max-width: 1024px` | `evaluators-ds.css` | `.bnd-grid` (bundle matches and build aside) becomes one column |
| Tailwind `sm` (`min-width: 40rem`, 640px at the default root size) | `components/app/user-menu.tsx`, `components/ui/dialog.tsx`, `components/ui/alert-dialog.tsx`, `components/review/export-dialog.tsx` | Shows the user's name beside the avatar; dialog width caps and footer row layout |
| Tailwind `md` (`min-width: 48rem`, 768px) | `components/ui/alert-dialog.tsx` | Description text wrapping (`text-pretty`) |
| `prefers-reduced-motion: reduce` | `evaluators-ds.css` | Turns off every transition and animation |

`globals.css` does not override Tailwind's `--breakpoint-*` values, so `sm` and `md` are Tailwind's
defaults.

## Primitives in `components/ui`

| File | Exports | Notes |
| --- | --- | --- |
| `frontend/components/ui/button.tsx` | `Button`, `buttonVariants` | shadcn Button with `cva` variants (`default`, `outline`, `secondary`, `ghost`, `destructive`, `link`) and sizes (`default`, `xs`, `sm`, `lg`, `icon`, `icon-xs`, `icon-sm`, `icon-lg`). Used only inside `dialog.tsx` and `alert-dialog.tsx`; app buttons use the `ev-btn` classes |
| `frontend/components/ui/dialog.tsx` | `Dialog`, `DialogContent` (with `showCloseButton`), `DialogDescription`, `DialogFooter`, `DialogHeader`, `DialogOverlay`, `DialogPortal`, `DialogTitle` | Radix Dialog; default width `sm:max-w-sm`, widened with `ev-dialog-wide` or `sm:max-w-lg` |
| `frontend/components/ui/alert-dialog.tsx` | `AlertDialog`, `AlertDialogAction`, `AlertDialogCancel`, `AlertDialogContent`, `AlertDialogDescription`, `AlertDialogFooter`, `AlertDialogHeader`, `AlertDialogOverlay`, `AlertDialogPortal`, `AlertDialogTitle` | Radix AlertDialog; used by `ConfirmDialog` on My documents |
| `frontend/components/ui/dropdown-menu.tsx` | `DropdownMenu`, `DropdownMenuTrigger`, `DropdownMenuContent`, `DropdownMenuLabel`, `DropdownMenuItem`, `DropdownMenuSeparator` | Radix DropdownMenu; the user menu and the My documents row menu |
| `frontend/components/ui/segmented-tabs.tsx` | `SegmentedTabs` | Not from shadcn. A controlled pill switch (`role="tablist"`, each button `role="tab"` with `aria-selected`); workbench tabs and bundle tabs |
| `frontend/components/ui/sonner.tsx` | `Toaster` | sonner toasts: light theme, 3500 ms, navy-900 surface, white text, lucide icons; placed `bottom-center` by the root layout |
| `frontend/components/ui/tooltip.tsx` | `TooltipProvider` | Only the provider (delay 200 ms, set in `Providers`). No Tooltip component exists; hover hints are native `title` attributes |

Imports use the `radix-ui` umbrella package (for example `import { Dialog as DialogPrimitive } from
"radix-ui"`). `cn()` in `frontend/lib/utils.ts` joins class names with `clsx` and resolves Tailwind
conflicts with `tailwind-merge`.

### shadcn CLI configuration (`frontend/components.json`)

| Key | Value |
| --- | --- |
| `style` | `radix-nova` |
| `rsc` | `true` |
| `tsx` | `true` |
| `tailwind.config` | `""` (Tailwind v4, no config file) |
| `tailwind.css` | `app/globals.css` |
| `tailwind.baseColor` | `neutral` |
| `tailwind.cssVariables` | `true` |
| `tailwind.prefix` | `""` |
| `iconLibrary` | `lucide` |
| `rtl` | `false` |
| `aliases` | `components` `@/components`, `utils` `@/lib/utils`, `ui` `@/components/ui`, `lib` `@/lib`, `hooks` `@/hooks` |
| `menuColor` | `default` |
| `menuAccent` | `subtle` |

## Related pages

- [The record workbench](../explanation/frontend-workbench.md)
- [How to extend the frontend](../how-to/extend-the-frontend.md)
- [Frontend routes and data reference](frontend-routes-and-data.md)

<!-- reviewed: 2026-09-30 -->
