---
name: Clauderdash
description: Personal observability instrument panel for Claude Code sessions.
colors:
  bg: "#080a0d"
  surface: "#0d1117"
  surface-2: "#141c24"
  surface-3: "#1c2530"
  border: "#243040"
  border-glow: "#364d66"
  text: "#dce6ef"
  text-dim: "#6e8aa0"
  text-subtle: "#405060"
  accent-cyan: "#22d3ee"
  accent-cyan-dim: "#0891b2"
  accent-blue: "#5b9bd5"
  accent-green: "#4dc88a"
  accent-red: "#e05252"
  accent-orange: "#f0944a"
  accent-purple: "#9b8aaa"
typography:
  display:
    fontFamily: "'Playfair Display', Georgia, serif"
    fontWeight: 600
    lineHeight: 1.1
  headline:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontWeight: 600
    fontSize: "1rem"
    lineHeight: 1.3
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontWeight: 400
    fontSize: "0.75rem"
    lineHeight: 1.6
  label:
    fontFamily: "'JetBrains Mono', Menlo, ui-monospace, monospace"
    fontWeight: 600
    fontSize: "0.625rem"
    letterSpacing: "0.2em"
    textTransform: "uppercase"
rounded:
  sm: "4px"
  md: "8px"
  lg: "12px"
  xl: "16px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-dim}"
    rounded: "{rounded.lg}"
    padding: "6px 12px"
  button-hover:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
  button-active:
    backgroundColor: "{colors.accent-cyan}"
    textColor: "{colors.bg}"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.text-subtle}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.xl}"
    padding: "{spacing.xl}"
---

# Design System: Clauderdash

## 1. Overview

**Creative North Star: "The Control Room"**

This is an instrument panel, not a reporting dashboard. Every surface is cool dark alloy — the color of a server chassis, a radar console, a place where systems are monitored not celebrated. The background is near-black with a barely perceptible cool cast, the way a monitor screen looks when it's the only light in a dim room. Signal Cyan is the one live readout color — the glow of an active LED indicator, a sonar ping, a cursor on a dark terminal. It never decorates; it informs.

The typography splits labor cleanly. JetBrains Mono carries all data: kickers, labels, numbers in context. Playfair Display carries only the stat that needs weight — the primary figure on a panel, the number that answers the glance. Inter handles prose and navigation. The combination reads as engineering documentation, not SaaS copy.

The layout is deliberately sparse between panels. Columns breathe. The empty space is not wasted; it is the negative space that makes the signal legible.

**Key Characteristics:**
- Cool-dark color field; cyan as the only assertive signal
- Three-font hierarchy: mono for data, display for primary stats, sans for text
- Tonal layering for depth; no box shadows anywhere
- Panels as instruments, not cards — kicker + title + one primary reading
- Hover states reveal depth (border brightens), not decoration

## 2. Colors: The Void Metal Palette

A single-accent restrained palette. Signal Cyan is the one voice. Everything else is tonal variation on a cool dark base.

### Primary
- **Signal Cyan** (`#22d3ee`): The sole assertive accent. Used on primary stat numbers, active states, and header identity. Its rarity is the point. On any given screen, cyan covers less than 10% of pixels.
- **Deep Cyan** (`#0891b2`): Dimmed cyan for secondary emphasis, hover borders, and non-critical cyan tints. Never used in place of Signal Cyan on primary readouts.

### Neutral
- **Void Black** (`#080a0d`): Page background. Has a barely perceptible cool cast — not neutral gray, not blue-black. The whole interface recedes to this.
- **Carbon** (`#0d1117`): Panel surface. The visible instrument face, raised one step from the page.
- **Cold Alloy** (`#141c24`): Secondary surface, used for inputs, segmented controls, and nested containers.
- **Deep Alloy** (`#1c2530`): Tertiary surface for deepest-nested elements.
- **Steel Seam** (`#243040`): Default border. The visible edge between surfaces.
- **Lit Edge** (`#364d66`): Hover border — brightens when an element is live.
- **Ice White** (`#dce6ef`): Primary text. Cool near-white, never pure white.
- **Steel Grey** (`#6e8aa0`): Dimmed text — secondary prose, narrative labels.
- **Deep Slate** (`#405060`): Subtle text — kickers, timestamps, decorative labels.

### Secondary (status accents)
- **Signal Blue** (`#5b9bd5`): Chart bars, secondary data series.
- **Signal Green** (`#4dc88a`): Positive/healthy states.
- **Signal Red** (`#e05252`): Errors, over-target states.
- **Signal Orange** (`#f0944a`): Warning-level emphasis.
- **Signal Purple** (`#9b8aaa`): Sonnet model family in charts.

### Named Rules
**The One Voice Rule.** Signal Cyan (`#22d3ee`) is used on ≤10% of any screen. One primary stat, one active button state, the header title. No cyan decorations, no cyan backgrounds on panels, no cyan text in prose.

**The Cool Precision Rule.** Every neutral has a barely perceptible cool blue cast (OKLCH hue 220–240). Never use warm greys, never introduce brown or amber tones. The coolness is the discipline.

## 3. Typography

**Display Font:** Playfair Display (with Georgia, serif fallback)
**Body Font:** Inter (with ui-sans-serif, system-ui, sans-serif fallback)
**Data/Label Font:** JetBrains Mono (with Menlo, ui-monospace, monospace fallback)

**Character:** The pairing reads as a technical logbook. Playfair's serifs give weight to the one number that matters; JetBrains Mono makes every data label feel like it came from an instrument, not a designer.

### Hierarchy
- **Display** (600 weight, 1.875rem / 30px, line-height 1.1): Primary stat numbers on panels. The number you read in a glance. Used via `.stat-num`. Only in cyan or status colors, never in neutral text.
- **Headline** (600 weight, 1rem / 16px, line-height 1.3): Panel titles. Inter, not Playfair. Short, noun-form.
- **Body** (400 weight, 0.75rem / 12px, line-height 1.6): Narrative text under stats (`.stat-narrative`). Steel Grey color. Max 60ch.
- **Label** (600 weight, 0.625rem / 10px, letter-spacing 0.2em, uppercase): Kickers, column headers, axis ticks. JetBrains Mono. Always Deep Slate color at rest.
- **Data** (400–500 weight, 0.75rem / 12px, tabular-nums): Values in tables and lists. JetBrains Mono. `font-variant-numeric: tabular-nums` always on.

### Named Rules
**The Mono-for-Data Rule.** Any value that changes dynamically — a number, a timestamp, a percentage, a file path — renders in JetBrains Mono, regardless of context. Never in Inter.

**The One Big Number Rule.** Each panel has at most one Display-size number. If a panel needs two big numbers, it needs to be two panels.

## 4. Elevation

This system is flat by default. No box shadows. Depth is expressed through tonal surface layering: `bg` → `surface` → `surface-2` → `surface-3`. Each step brightens by the same cool increment. The eye reads depth from tone, not from cast shadow.

Border glow (`border-glow: #364d66`) is the only state-based depth cue — it activates on hover to signal interactivity without lifting the element.

**The No-Shadow Rule.** `box-shadow` is prohibited on panels, cards, buttons, and containers. Shadow implies float; nothing in this UI floats. The surface is a console, not a collection of paper cards.

## 5. Components

### Panels
The fundamental container. Not a "card" — it's an instrument face.
- **Shape:** Gently curved corners (16px radius, `rounded-2xl`).
- **Background:** Carbon (`#0d1117`), one step above the page floor.
- **Border:** Steel Seam (`#243040`) at rest; Lit Edge (`#364d66`) on hover. 1px, no glow.
- **Internal padding:** 24px uniform (`p-6`).
- **Header:** Kicker above title. Kicker is Label size in Deep Slate. Title is Headline in Ice White.
- **Animation:** Fade-in on mount (220ms ease-out, 4px Y translate). No other motion.

### Buttons (`.btn`)
- **Shape:** Rounded corners (8px, `rounded-lg`).
- **Default:** Cold Alloy background, Steel Seam border, Steel Grey text.
- **Hover:** Border lifts to Lit Edge, text brightens to Ice White.
- **Active / selected:** Signal Cyan border at 50% opacity, Signal Cyan background at 10%, text in Signal Cyan.
- **No filled/primary button variant** for UI controls. Actions are signals, not calls to action.

### Segmented Controls (RangeToggle)
- **Container:** Cold Alloy background, Steel Seam border, 8px radius, 2px padding.
- **Segment:** 10px mono uppercase, 10px horizontal padding, 4px vertical.
- **Selected:** Signal Blue at 15% background, Signal Blue text.
- **Unselected:** Deep Slate text, lifts to Steel Grey on hover.

### Chips (`.chip`)
- **Shape:** 4px radius.
- **Style:** Colored background at 10% opacity, matching text at full opacity.
- **Use:** Status badges, "Over target" labels. Never decorative.

### Kickers (`.kicker`)
10px JetBrains Mono, uppercase, 0.2em letter-spacing, Deep Slate. The panel's section identity. Format: `NN · LABEL` (e.g. `01 · SYSTEM`).

### Skeleton / Loading States
Animated pulse on Cold Alloy blocks, staggered 80ms per row. Pure function — no spinner, no text.

### Empty States
Dashed border (Steel Seam at 60% opacity), centered Label text in Deep Slate. Never an illustration, never an icon.

### Charts (Recharts)
- **Grid lines:** Steel Seam, horizontal only (`vertical={false}`).
- **Axis ticks:** Deep Slate, 10px JetBrains Mono.
- **Tooltip:** Carbon background, Steel Seam border, 8px radius, 12px sans.
- **Bar cursor highlight:** Signal Cyan at 5% opacity.
- **No animation** (`isAnimationActive={false}` on all chart elements).

## 6. Do's and Don'ts

### Do:
- **Do** use JetBrains Mono for every dynamic value — numbers, timestamps, file paths, percentages.
- **Do** limit Signal Cyan to one primary stat per panel and active UI states.
- **Do** express depth through surface toning (`surface` → `surface-2` → `surface-3`), never shadows.
- **Do** use noun-form labels: "Burn", "Discipline", "Efficiency" — not "Current burn rate" or "How efficient?"
- **Do** keep panel titles short (1-2 words). The kicker carries the category context.
- **Do** use `tabular-nums` on all numerical values so columns align.
- **Do** animate only on mount (fade-in). No hover animations, no transition on layout properties.
- **Do** use dashed borders for empty states. Absence of data should feel intentional, not broken.

### Don't:
- **Don't** use `box-shadow` on any surface. This system has no floating elements.
- **Don't** use warm greys or any amber/brown tones. Every neutral must be cool-tinted.
- **Don't** use gradient text (`background-clip: text`). Emphasis via weight or Signal Cyan.
- **Don't** use `border-left` greater than 1px as a colored accent stripe. Full borders or nothing.
- **Don't** build a panel that looks like a SaaS metric card: big number, small label, gradient accent. That is the hero-metric template. Prohibited.
- **Don't** use blue as a dominant accent. Signal Blue is for chart series only.
- **Don't** put two Display-size numbers in one panel. One reading per instrument face.
- **Don't** use glassmorphism, blur effects, or translucent overlays as decoration.
- **Don't** write marketing copy in the UI. No subtitles that restate the title.
- **Don't** add motion to chart renders (`isAnimationActive` stays `false`). Data is not entertainment.
