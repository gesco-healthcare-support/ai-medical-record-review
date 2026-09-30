# frontend/components/ui - agent instructions

## Rules

- These files are shadcn/ui output (`frontend/components.json`: style `radix-nova`, Tailwind v4 with
  CSS variables, `@/components/ui` alias). Prefer regenerating with the CLI over hand-editing; if you
  hand-edit, keep the diff small so a later regeneration is reviewable.
- Radix is imported from the `radix-ui` umbrella package (`import { Dialog as DialogPrimitive } from
  "radix-ui"`), not from `@radix-ui/react-*`.
- Colours come from the semantic slots in `frontend/app/globals.css` (`bg-primary`, `bg-popover`,
  `text-muted-foreground`, `bg-destructive` ...). Never put hex values here; change the slot mapping
  instead.
- The app is light-only. Do not reintroduce `next-themes` or a theme switch in `sonner.tsx`; the toaster
  pins `theme="light"`. `next-themes` is still listed in `package.json` but imported nowhere.
- There is only `TooltipProvider`. Adding a Tooltip component is a design change: every hint today is a
  native `title` attribute, and tests assert on it (for example the disabled-button reasons in
  `components/review/review-page-client.test.tsx`).
- `SegmentedTabs` is controlled: the parent owns `value`. It is used both for in-page tabs (the
  workbench) and for tabs that navigate (the bundle pages); keep it free of routing.
- Dialog width defaults to `sm:max-w-sm`. Widen through `className` (`ev-dialog-wide`, `sm:max-w-lg`)
  rather than editing the default.
- Toast placement (`bottom-center`) is set in `frontend/app/layout.tsx`, not here; `layout.test.tsx`
  pins it and the navy styling.

## Commands

Run from `frontend/`:

```bash
pnpm exec shadcn add popover
pnpm typecheck
pnpm exec vitest run app components
```

(`shadcn add` writes a new file here; swap in the primitive you need.)

## Docs

- `docs/reference/design-system.md` (bridge, primitives, breakpoints)
