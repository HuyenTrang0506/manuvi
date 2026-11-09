# Web UI design notes

## Identity
Manuvi translates technical manuals, so the UI behaves like a proofing desk: cool proof-paper neutrals, one accent (process cyan), and two typefaces served locally (`static/fonts`, OFL, Latin and Vietnamese subsets): **IBM Plex Serif** for headings and for the document text in the EN/VI panes, **Inter** for the interface. Tokens live in `static/brand.css` (`:root`, redefined for dark).

Signature details, each carrying information:
- **Page-label chips** (`23-4`) as printed in the manual. The job page shows one chip per printed page, filled by state (translated, needs attention, waiting), so progress is per page, priority sections first.
- **Protected codes as ink pills** (part numbers, wire codes, values with units, `Fig.` refs) in the EN and VI panes. Display only; the text is unchanged and selectable.
- **EN / VI side by side**, language-tagged (`lang`), serif text at reading size.
- **Hazard-stripe edge** on safety segments, plus the word "Safety" and a warning glyph.
- Wordmark: an inline SVG page with two bars (EN in ink, VI in accent) and "manuvi" in Plex Serif Bold.

Motion is answer-to-action only, 150 to 250 ms: status colour changes, drop zone, row reveal, approve flash, progress width, skeleton shimmer while loading. `prefers-reduced-motion` turns all of it off.

## Hard rules (tested in `tests/webapp/test_design_rules.py`)
| Rule | Why it is hard |
|---|---|
| WCAG 2.1 AA contrast in both themes: text 4.5:1, UI borders, icons, status glyphs and the focus ring 3:1, button text 4.5:1 | Reviewers read dense text for hours; an unreadable pair is a defect, not a taste |
| Colour is never the only carrier: every status has a word and its own glyph; errors, notes and safety carry an icon and label; page chips carry a glyph | About 1 in 12 men are colour blind; safety meaning must survive greyscale |
| Semantic hues fixed: error red, success green, warning amber, info blue; accent stays 25 degrees away from them; `--safety` and `--code-*` are used only by safety and code components | One colour, one meaning, everywhere |
| Every colour is a token on `:root`; dark redefines all of them; no literal colour in components; `body` has an explicit background | Theming without regressions |
| Max two typefaces, a defined rem type scale, body 16px, line height 1.4 to 1.6, line length at most 75ch, spacing on the 4/8 px scale | Consistency and readability |
| Targets at least 44px, visible focus ring, never `outline: none`, hover/active/disabled states on controls, reduced motion respected, no `order` or reversed flex (reading order = DOM order), no zoom blocking, text in rem so 200% works | Keyboard, touch and low-vision users |
| Success and error stay apart under simulated deuteranopia and protanopia (CIE76 delta E at least 20) and differ by glyph and lightness | The most common colour-blindness pairs |
| No CDN, no inline script or style (strict CSP) | Security, offline use |

Other web rules that are enforced elsewhere: single-character shortcuts are opt-in (WCAG 2.1.4), dynamic status messages use `role=status`, errors use `role=alert` and move focus to the field.

## Soft rules (guidelines, not tested)
- 60-30-10 balance: about 60% paper and sheet, 30% ink and rules, 10% accent and semantic colour.
- Harmony: near-monochrome cool neutrals with a single saturated accent; semantic hues are used only where they mean something.
- Accent saturation: deep and slightly desaturated in light, lighter in dark so it holds 3:1 on both surfaces.
- Spacing rhythm: 4/8 scale; larger gaps (24, 48) separate sections, no golden-ratio scaling.
- Radius personality: 4 and 8 px, precise rather than pill-soft; pills are reserved for protected codes.
- Easing: one ease-out curve, 150 ms for hover, 220 ms for state changes.
- Illustration: none; the manuals' own page labels and codes are the decoration.
- Copy tone: plain verbs, sentence case, errors say what happened and what to do, an action keeps its name through the flow ("Start translation", "Continue after my approvals").

## Accessibility review (WCAG 2.1 AA, self-audit with the `design:accessibility-review` checklist)
Checked by code reading, headless Chrome screenshots at 400 px and 1280 px in both themes (no horizontal scroll), and the tests above.
Fixed during the review: programmatic focus ring on `main`, invalid `aria-selected` on list rows (now `aria-current`), single-key shortcuts always on (now opt-in), stale toast text, disabled download buttons without a spoken reason.
Open and minor: status glyphs are CSS generated content and may be read aloud next to the word; a manual pass with NVDA or VoiceOver is still needed (automated checks find about a third of issues).
