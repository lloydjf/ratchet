---
name: Ratchet
description: Regression CI for self-improving agents, drawn as a machined decision instrument in graphite steel.
colors:
  page: "#201e1b"
  panel: "#272522"
  raised: "#2f2c29"
  hairline: "#3a3733"
  ink: "#eceae6"
  ink-secondary: "#a8a49e"
  ink-tertiary: "#6f6b66"
  steel: "#93a9bf"
  pass: "#5cb87f"
  warn: "#d6a23e"
  block: "#ea6d55"
  drift: "#72a9de"
  nano: "#4cbfc4"
  super: "#a68ee8"
  ultra: "#de80b4"
  block-ink: "#f0806b"
  super-ink: "#b19deb"
  page-light: "#f3f1ec"
  panel-light: "#ffffff"
  raised-light: "#f7f5f1"
  hairline-light: "#e6e3dd"
  ink-light: "#1d1b18"
  ink-secondary-light: "#5c5852"
  ink-tertiary-light: "#9a958e"
  steel-light: "#3d5a77"
  pass-light: "#1d7a45"
  warn-light: "#8a5b00"
  block-light: "#b93a22"
  drift-light: "#1f62a8"
  nano-light: "#0b767c"
  super-light: "#6a4bc6"
  ultra-light: "#a63a77"
  pass-ink-light: "#1a6e3e"
  warn-ink-light: "#7a5000"
  block-ink-light: "#a8331d"
  drift-ink-light: "#1b5896"
  nano-ink-light: "#096a70"
  super-ink-light: "#5f42b5"
  ultra-ink-light: "#96336b"
typography:
  display:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "56px"
    fontWeight: 600
    lineHeight: "60px"
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: "32px"
    letterSpacing: "-0.01em"
  title:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: "28px"
    letterSpacing: "-0.01em"
  section:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: "24px"
  prose:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: "24px"
  body:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: "18px"
  label:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "16px"
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "16px"
    fontFeature: "tabular-nums"
  reading:
    fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: "26px"
rounded:
  none: "0px"
  kbd: "4px"
  sm: "6px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  2xl: "56px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.page}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "36px"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "36px"
  button-secondary-hover:
    backgroundColor: "{colors.raised}"
  button-danger:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.block-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "36px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "36px"
  button-ghost-hover:
    backgroundColor: "{colors.raised}"
  button-small:
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.none}"
    padding: "24px"
  chip-pass:
    textColor: "{colors.pass}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0 8px"
    height: "24px"
  chip-warn:
    textColor: "{colors.warn}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0 8px"
    height: "24px"
  chip-block:
    textColor: "{colors.block-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0 8px"
    height: "24px"
  chip-drift:
    textColor: "{colors.drift}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0 8px"
    height: "24px"
  chip-neutral:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0 8px"
    height: "24px"
  kbd:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.kbd}"
    padding: "0 5px"
    height: "20px"
  tooltip:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "8px 12px"
  input-palette:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.section}"
    rounded: "{rounded.none}"
    padding: "0"
    height: "48px"
---

# Design System: Ratchet

## Overview

**Creative North Star: "The Machined Decision Instrument"**

Ratchet shows an engineer whether an agent's edit to its own skill made the agent worse, and it shows the decision being made. The visual system is built around one instrument, the cascade sieve: every replayed task is a dot that enters from the left and hops across four columns (Level 0, Nano, Super, Ultra) until a judge settles it, and it comes to rest as a ring in its outcome colour. Everything around that instrument is quiet, dense and exact. The result reads like a well-made panel in a workshop: graphite surfaces, hairline rules, one steel accent, and colour reserved for the things that mean something.

The world is dark by default (graphite, not black) with a light scheme that is the same system on a cool off-white. Surfaces step up in three tones (page, panel, raised) and are bounded by 1-px hairlines; nothing casts a shadow and nothing is a gradient. Type is IBM Plex throughout: Sans for everything that is read, Mono only for identifiers, arguments, diffs, hashes and figures. There is exactly one display-size moment, the decision word, and it is the only place the type gets loud.

The same tokens dress three surfaces: the operator dashboard, the public landing page with its recorded verdict, and the documentation site (which keeps its MkDocs wayfinding and takes the frame, type, hairlines and outcome colours from this system). The sieve is the signature across all three, from the full animated instrument on the verdict page to a 28 by 14 settled glyph beside the wordmark in the docs masthead.

**Key Characteristics:**
- Graphite dark default; light is the same geometry on off-white, never a separate design
- Decision hues (pass, warn, block, drift) and tier hues (nano, super, ultra) are the only saturated colours
- One desaturated steel accent, used for focus, links, caret, selection and the hovered route in the sieve
- Hairline panels on an 8-px construction grid; no shadows, no gradients, no cards within cards
- IBM Plex Sans 13/18 UI density; Plex Mono with tabular figures wherever a number can change
- Square corners on surfaces; 6-px radius only on things you press
- Motion is authored per moment and short (160 to 320 ms); the stall pulse is the only loop

## Colors

A graphite neutral ramp with one steel accent, and seven meaning-carrying hues that appear only as tints, strokes and inks, never as flood fills.

### Primary
- **Steel** (`steel`): the single accent. Focus ring, text links, caret, selection highlight, the active TOC marker in the docs, the hovered dot's route in the sieve, and the solid primary button on the landing page. It is deliberately desaturated so it never competes with a decision colour.

### Secondary: decision hues
- **Pass green** (`pass`): a pair that held or improved; the PASS decision.
- **Warn amber** (`warn`): uncertain outcomes; the WARN (held) decision; the simulated badge; the control-replay marker.
- **Block red** (`block`): a regression; the BLOCK decision; the danger button's border; deletions in a diff.
- **Drift blue** (`drift`): a world-drift outcome and the Tavily marker. Drift is not a decision colour; it never fills a band.

### Tertiary: tier hues
- **Nano teal** (`nano`), **Super violet** (`super`), **Ultra magenta** (`ultra`): the three judge tiers. They ink the sieve's column lines, the cost ticker's bar-fields, the tier badges and the per-column captions. Level 0 is model-free and takes the secondary ink instead of a hue.

Each hue has an **ink** partner for text on its own tint. In the dark scheme the ink equals the hue wherever the hue already clears 4.5:1 on the panel; only `block-ink` and `super-ink` lift slightly. In the light scheme every hue has its own darker ink (`pass-ink-light` through `ultra-ink-light`).

### Neutral
- **Page** (`page` / `page-light`): the document background and the mobile sheet backdrop (at 80 %).
- **Panel** (`panel` / `panel-light`): the rail, every content panel, the dialog, the inline confirm.
- **Raised** (`raised` / `raised-light`): hover and selected rows, the tooltip, the skeleton, inline code, the neutral chip.
- **Hairline** (`hairline` / `hairline-light`): every 1-px rule, panel border and divider. A stronger hairline for interactive borders and the scrollbar thumb is mixed at runtime: secondary ink at 58 % (dark) or 62 % (light) over the hairline.
- **Ink** (`ink`), **secondary ink** (`ink-secondary`), **tertiary ink** (`ink-tertiary`): primary text, supporting text and labels, and the quietest level (skipped stages, passed TOC entries, the sieve's entry marks). Tertiary ink is below 4.5:1 and is used for graphics and de-emphasis, not for running text.

Runtime mixes, all derived with `color-mix(in srgb, …)` from the tokens above: chip tint = hue at 12 % over the panel with a border of hue at 70 %; decision band = hue at 6 % over the panel; diff add and delete = pass or block at 11 %; highlight = warn at 9 %; flash = steel at 22 %; selection = steel at 34 % over transparent; the sieve's construction grid = ink at 5 %.

### Named Rules
**The Only-Saturated Rule.** Decision and tier hues are the only saturated colours on any screen. If a new element wants colour and it is not an outcome, a decision or a tier, it gets ink, secondary ink or steel.

**The Six Percent Rule.** The decision band is the only filled colour area in the product, and it carries a 6 % tint of its decision hue over the panel. No other surface is tinted by a decision colour beyond the 12 % chip tint.

**The Word Beside the Colour Rule.** Colour never carries meaning alone. Every decision chip, outcome chip and tier tag pairs its hue with its word and a drawn mark.

## Typography

**Display Font:** IBM Plex Sans (with ui-sans-serif, system-ui fallbacks)
**Body Font:** IBM Plex Sans
**Label/Mono Font:** IBM Plex Mono (with ui-monospace, Menlo fallbacks)

**Character:** One family, two cuts. Plex Sans at 400, 500, 600 and 400 italic does all the reading; Plex Mono at 400 and 500 does the identifiers and the figures. The pairing is matter-of-fact and slightly engineered: Plex's open apertures hold up at 13 px, and its mono shares the Sans's proportions so numbers sit in prose without a jolt. All six faces are self-hosted as latin-subset WOFF2 with `font-display: swap`; there are no third-party font hosts.

### Hierarchy
- **Display** (600, 56/60, -0.02em): the decision word ("Blocked.", "Held.", "Passed.") in the decision band, coloured in the decision's ink. This is the only place display size appears.
- **Headline** (600, 24/32, -0.01em): page titles in the dashboard.
- **Title** (600, 20/28, -0.01em): the running stage inside the band before a verdict settles; section headings on the landing page (at 500).
- **Section** (600, 15/24): panel headings and in-panel section titles, sentence case. The small heading inside a panel is 600 at 13/18.
- **Prose** (400, 15/24): narrative text, reasons, explanations; max measure 68 ch in the dashboard.
- **Body** (400, 13/18): the UI default. The root stays 16 px so the rem spacing scale lands on the 8-px grid while text is set at 13/18.
- **Label** (400 or 500, 12/16): metadata, captions, column captions, chip text.
- **Mono** (400, 12/16, tabular): ids, args, hashes, diffs, costs, counts. Mono inside a chip or tier tag is 500.
- **Reading** (400, 16/26): the docs column and the benchmark report, measure 70 ch; docs headings step 32/40, 22/30, 18/26, 16/24 at 600 with h2 separated by a hairline above.

### Named Rules
**The Mono-Is-Data Rule.** Plex Mono is for ids, args, diffs, hashes, commands and figures. It is never used for labels, headings or metadata prose. The one exception is the docs' outcome and decision tags, which are mono because they quote the product's own vocabulary.

**The Tabular Rule.** Wherever a number can change (costs, counts, confidences, timestamps), set `font-variant-numeric: tabular-nums`. The landing page and bench report set it on the body; the dashboard applies it with `.num`.

**The One Display Moment Rule.** Display size (56/60) appears in exactly one place per surface: the decision word. Headings never exceed 32 px.

**The Sentence Case Rule.** No all-caps, no eyebrow labels, no section numbering. Headings and labels are sentence case; the only uppercase strings are literal abbreviations (SIM, L0, N, S, U).

## Layout

Everything sits on an 8-px construction grid; the component kit can reveal it as a true overlay state (an 8-px pattern with a 64-px major). Spacing steps are 4, 8, 16, 24, 32 and 56 px; panel padding is 24 px (16 px under 768 px).

The dashboard shell is a persistent left rail and one content column. The rail is 220 px wide at and above 1101 px (the `rail` breakpoint, 68.8125 rem), collapses to a 56-px icon rail between 768 and 1100 px, and becomes a 280-px sheet sliding in from the left on mobile with a 48-px sticky top bar. The rail holds the logo, primary navigation, the simulated badge when relevant, and the cost ticker at the bottom. The content column is centred with a max width of 1120 px and 32-px side padding on desktop (16 px on mobile). Pages are a vertical stack of panels separated by 1-px hairlines: the stack itself is drawn as a hairline-coloured container with 1-px gaps, so panels never double their borders.

The landing page uses the same 1120-px wrap with a 32-px gutter and a 64-px top strip. Its hero is a 5/12 copy column beside a 7/12 recorded-verdict panel, collapsing to one column under 1080 px; below the fold, content is set as hairline-separated rows (a 160-px label column, text, and a 400-px proof column). The docs site keeps Material's grid at a 1320-px max width with a 232-px primary sidebar bounded by a right hairline and a 70-ch reading column with 40-px margins (32 px below 1220 px, 16 px below 719 px).

Responsive thresholds in use: 1101 px (full rail), 1080 px (landing hero stacks), 768 px (icon rail, panel padding steps down), 720 px (landing gutters to 16 px, verdict panel bleeds), 560 px (the sieve enters its narrow layout), 390 px is the mobile target.

## Elevation & Depth

This system has no shadows. Depth is conveyed entirely by surface step and hairline: the page is the lowest tone, panels sit one step up, and hovered, selected or floating elements (tooltips, the active palette row, the raised code background) sit one more step up. Overlays (the command palette, dialogs, the mobile sheet) float on a page-coloured scrim at 80 % opacity and are bounded by a strong hairline; they gain no shadow. The docs theme explicitly zeroes Material's shadow levels and the header's scroll shadow.

### Named Rules
**The No-Shadow Rule.** No element casts a box-shadow or text-shadow on any surface. If a floating element needs separation, use a strong hairline border and the raised surface tone.

**The Surface Step Rule.** Panels are distinguished from the page by one tone step and a hairline, never by both a step and a shadow, and never by a gradient.

## Shapes

Surfaces are square. Panels, bands, chips, tags, tooltips, dialogs, code blocks, tables, figures and the inline confirm all have a 0-px radius with a 1-px hairline border. The only rounded corners are on things you press: buttons, nav items, icon buttons, the palette's selected row, the docs search field and the back-to-top control take a 6-px radius; `kbd` keycaps take 4 px; the scrollbar thumb takes 6 px. Links are underlined with a 1-px line offset 3 px below the baseline, with the underline colour at 45 % steel on the landing and docs surfaces and only on hover in the dashboard.

Recurring silhouettes are drawn, not iconic: the sieve's dots are 10-px rings (a 4-px radius circle stroked 2 px in the outcome colour, filled with the panel colour), their routes are 1-px polylines on 45° and 90° turns, the judge-trail glyph is four 18 by 6 rectangles with 3-px gaps, the cost ticker's bars are fields of 40 one-pixel ticks, and skipped or failed states are dashed (`2 2`) outlines rather than greyed fills. Marks inside chips come from a 2-px-stroke line icon set drawn on a 24-grid (check, x, pause, waves, arrow-up, circle-help, minus); no Unicode glyphs or emoji stand in for them.

## Components

### Buttons
- **Shape:** Rounded only here (6 px); 36 px tall at the default size, 32 px small, 44 px minimum on touch layouts; 1-px border always present.
- **Primary (dashboard):** ink fill with page-coloured text and an ink border (`button-primary`); hover darkens the fill to ink at 88 % over the page. Used for approve and bless.
- **Primary (landing):** solid steel with a dark on-steel text colour, 40 px tall, 15-px medium label; hover lifts to a lighter steel, active nudges 1 px down.
- **Secondary:** panel fill, ink text, strong-hairline border; hover raises to the raised tone. The default variant.
- **Danger:** panel fill, block-ink text, border of block at 70 % over the panel; hover tints block at 10 %. Used for roll back, unbless and approve-anyway (overriding a block is a careful action, not a primary one).
- **Ghost:** transparent, ink text, transparent border; hover raises.
- **Transitions:** background and border colour over 160 ms. Disabled is 50 % opacity with `not-allowed`.

### Chips
- **Decision chip:** BLOCK / WARN / PASS as a drawn mark plus its word, 20, 24 or 32 px tall by size, 12-px medium (13-px semibold at large), on a 12 % tint of the decision hue with a border of the hue at 70 % and the hue's ink for text. Square corners. A muted (not yet decided) chip is the neutral chip with a dashed border. When a decision first appears it fades in over 160 ms.
- **Outcome chip:** the pair outcome (held, got worse, world changed, improved, uncertain, failed) in the same construction; failed uses the neutral chip.
- **Tier tag:** the tier letter or name in mono 500, tinted in its tier hue with a solid hue border.
- **Docs tags:** the same vocabulary in the reading column as 12/18 mono 500 on a transparent ground with a 1-px border at 42 % of the text colour; skipped tiers are dashed.

### Cards / Containers
- **Corner style:** square (0 px).
- **Background:** panel tone; a hovered or selected row steps to raised.
- **Shadow strategy:** none (see Elevation & Depth).
- **Border:** 1-px hairline; interactive or floating containers use the strong hairline.
- **Internal padding:** 24 px (16 px on mobile); dialog sections use 20 px horizontal and 16 px vertical, separated by hairlines.

### Inputs / Fields
- **Style:** the command palette input is bare (transparent, 48 px tall, 15/24 ink text, no outline) inside a hairline-bounded header; the docs search is a 32-px field on the page tone with a hairline border and 6-px radius that fuses with the results panel when open. The install command on the landing page is a 40-px mono field on the panel tone whose border strengthens on hover.
- **Caret and placeholder:** caret in steel, placeholder in secondary ink.
- **Focus:** the global ring (below); the palette input relies on the dialog's strong hairline instead.
- **Error:** inline, as a warn-tinted hairline row inside the control that failed; never a modal.

### Navigation
- **Rail:** panel tone with a right hairline, 220 px wide; the logo lockup at the top in a 56-px row; nav items are 13/18 ink-secondary text that becomes ink on hover and ink at 600 when active, with a 6-px radius hit area. The icon rail shows marks only. The bottom of the rail holds the simulated badge and the cost ticker above a top hairline.
- **Landing top strip:** 64 px tall, hairline below, mark plus wordmark left, three 500-weight text links right.
- **Docs masthead:** mark, wordmark, settled-sieve glyph and the "Docs" label on the panel tone with a hairline below; the left nav tree is hairline-sectioned 14/20 with active entries at 600; the right TOC marks the active entry with a 2-px steel left rule.
- **Mobile:** a 48-px sticky bar with a menu button and the logo; the rail becomes a 280-px sheet that slides in 16 px over 240 ms.

### Cascade Sieve (signature)
The verdict watched being decided. A full-width field 220 px tall on a visible 8-px construction grid (ink at 5 %). Four columns, Level 0, Nano, Super, Ultra, each a 2-px vertical line in its column ink (secondary ink for Level 0, the tier hue for the judges) with a 600-weight caption above and tabular counts and cost below. A dashed entry line at the left carries the entered count. Dots enter 24 px from the left and hop one column per cascade event on 45°/90° routes, 240 ms per hop with the standard ease-out; settled dots become 10-px rings in their outcome colour with their route left behind as a 1-px line. A hovered or focused dot gains an 8-px steel ring and recolours its route steel; a tooltip on the raised tone names the task, outcome with its mark, deciding stage and cost. A right-hand "decided" rail (192 px) tallies outcomes with rings. After the verdict settles the sieve freezes. A static thumb (112 by 36) represents a verdict in timeline rows and the kit; the landing page ships the settled sieve as inline SVG; the docs masthead carries it as a 28 by 14 glyph.

### Decision Band (signature)
The top panel of a verdict. While running it shows the current stage at title size. On `verdict.decided` the band cross-fades over 320 ms: the stage line fades out, the panel background transitions to the 6 % decision tint, and the decision word enters at display size in the decision's ink with its reason sentence below at prose size and the action control at the band's right edge. On the landing page the same band settles once on load (word from 40 % opacity and 4 px below) and is skipped under reduced motion.

### Judge-Trail Glyph (signature)
Four segments, L0 N S U, each an 18 by 6 rectangle with 3-px gaps and a 10-px mono letter beneath; filled in the outcome colour up to the stage that decided the pair, dashed in the strong hairline where the cascade did not go; the deciding stage's letter is ink, the rest secondary. A mono T marks a Tavily drift check and a mono C in warn ink marks a control-replay decision. Hover or focus opens a 300-px popover listing each tier's verdict, confidence and first reason.

### Cost Ticker (signature)
In the rail: the label, the live total in 15-px mono 500, and three rows of tier letter, hairline bar-field and tabular cost. Each bar-field is 112 by 8 px of 40 one-pixel ticks filled in the tier hue up to the value; all three share one scale. While a run is spending, every figure interpolates over 300 ms and a "live" label appears in pass ink. Hover or focus adds the all-Ultra counterfactual as a muted fourth bar and rescales the field over 300 ms. The icon rail shows the three bars at 28 px with a 6-px pass dot when live.

### Inline Confirm
Roll back, approve, re-bless, bless and unbless never open a modal. The button expands in place (fade and 2-px rise over 160 ms) into a 380-px-max hairline box on the panel tone: a 13-px semibold title, a 12-px body, the exact git command in mono inside a page-toned hairline well, then Cancel (focused) and the confirm button in the action's variant. Errors render inline as a warn-tinted row.

### Command Palette
Opened with ⌘K / Ctrl K: a dialog 12 vh from the top on an 80 % page scrim, strong hairline, panel tone. A bare 48-px input with a search mark and an `Esc` keycap, then grouped rows (36 px minimum, 6-px radius, raised tone when current) with a 12-px secondary hint right-aligned, and a hairline-topped footer of keycaps. Skill names in rows are mono.

### Tooltip and Keycap
Tooltips are 12/16 ink on the raised tone with a strong hairline, square, 8 by 12 px padding. Keycaps are 20-px mono at 11 px, secondary ink on the panel tone, strong hairline, 4-px radius.

### States
- **Loading:** skeleton blocks in the raised tone, square, sized to the content they replace.
- **Stalled:** the only looping motion, a 1.2-s opacity pulse between 1 and 0.45.
- **Evidence flash:** a 160-ms linear flash of steel at 22 % with a 1-px inset steel outline when a claim scrolls to its evidence.
- **Toast and confirm:** 160-ms fade with a 4-px rise (toast) or 2-px drop (confirm).
- **Empty and error:** text on the panel; errors use the warn tint row, never a red flood.

### Browser Surfaces
Selection is steel at 34 % with ink text; the caret is steel in every field; the scrollbar is a 10-px thumb in the hairline tone with a 2-px page-coloured inset and 6-px radius (strong hairline on hover) on a transparent track, with `scrollbar-color` set to match; the focus ring is a 2-px solid steel outline offset 2 px on every `:focus-visible`. Links sit on a 1-px underline offset 3 px.

### Logo
The mark is a ratchet wheel cut from a rounded square (244 by 244 on a 260 viewBox, 64 radius; ring cut-out 22 radius). On product surfaces it is drawn inline from the current theme's tokens: square in ink, wheel in the page colour, so it inverts with the scheme. Static copies exist as mark (light square, dark wheel) and mark-inverse, plus glyph light/dark, wordmark light/dark and lockup on light/dark; the landing and docs pages swap between the pair with `prefers-color-scheme` or the docs scheme attribute. The wordmark is the only lettering treatment for the name and is never retyped in Plex. Sizes in use: mark 24 px with the wordmark at 15 to 17 px tall and a 10-px gap (the rail, the landing strip, the docs masthead); mark 28 px in the docs header; the mark alone as favicon and in the icon rail. The sieve glyph (28 by 14) may sit beside the lockup in a masthead; nothing else is added to the lockup.

## Do's and Don'ts

### Do:
- **Do** take every colour from the token set; derive tints with `color-mix` at the established ratios (12 % chip, 6 % band, 11 % diff, 9 % highlight, 22 % flash, 34 % selection).
- **Do** separate panels with 1-px hairlines and a one-step surface change; stack panels with a 1-px hairline gap so borders never double.
- **Do** keep the 8-px grid: panel padding 24 px, row padding 12 px, gaps of 8, 16, 24 and 32 px; check against the kit's grid overlay.
- **Do** set Plex Sans 13/18 for UI, 15/24 for prose at 68 ch, 12/16 for metadata, 16/26 for documentation at 70 ch.
- **Do** use Plex Mono with tabular figures for every id, hash, command, diff, cost and count.
- **Do** pair every decision, outcome and tier colour with its word and a drawn 2-px-stroke mark.
- **Do** give interactive elements a 6-px radius, 36-px height (44 px on touch) and the 2-px steel focus ring offset 2 px.
- **Do** author motion per moment: 240 ms per sieve hop, 320 ms band cross-fade, 160 ms flash and stage fades, 300 ms ticker interpolation, all on `cubic-bezier(0.16, 1, 0.3, 1)`; collapse every animation and transition to 0.01 ms under `prefers-reduced-motion: reduce` and skip the landing band settle entirely.
- **Do** keep the light scheme as the same geometry: swap tokens, never restyle.
- **Do** keep MkDocs wayfinding (left tree, search, right TOC, prev/next) on the docs site; the world enters through tokens, type, hairlines and tags only.

### Don't:
- **Don't** add box-shadows, text-shadows or gradients anywhere, including overlays; use a strong hairline and the raised tone.
- **Don't** round panels, chips, tags, bands, tooltips, dialogs, code blocks or tables; square is the default and 6 px is reserved for things you press.
- **Don't** fill any area with a decision colour beyond the band's 6 % tint; drift never fills a band.
- **Don't** introduce a second accent; steel is the only accent and it is reserved for focus, links, caret, selection and the hovered route.
- **Don't** set headings, labels or metadata in Plex Mono, and don't retype the wordmark in Plex.
- **Don't** use all-caps, eyebrow or kicker labels, numbered section headings, or display size anywhere but the decision word.
- **Don't** use Unicode glyphs, emoji or icon fonts for marks; draw them from the 2-px line icon set as inline SVG.
- **Don't** open a modal for an action; confirm in place and show the exact git command.
- **Don't** use rounded progress bars or filled meters; the only bars are hairline tick fields.
- **Don't** load fonts or scripts from third-party hosts; all six Plex faces are self-hosted.
