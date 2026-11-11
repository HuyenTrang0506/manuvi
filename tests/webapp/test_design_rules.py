"""Hard design rules, checked from the CSS/JS/HTML sources (no browser). Soft rules live in docs/DESIGN-NOTES.md."""
import colorsys
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "webapp" / "static"
CSS = (STATIC / "brand.css").read_text("utf-8")
PAGES = {n: (STATIC / n).read_text("utf-8") for n in ("login.html", "portal.html", "index.html", "viewer.html")}
JS = {n: (STATIC / n).read_text("utf-8") for n in ("common.js", "portal.js", "review.js", "login.js", "viewer.js")}


# ---- parsing
def _block(text: str, start: int) -> str:
    depth, i = 0, text.index("{", start)
    j = i
    while True:
        depth += text[j] == "{"
        depth -= text[j] == "}"
        j += 1
        if depth == 0:
            return text[i + 1:j - 1]


def _tokens(body: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", body)}


LIGHT = _tokens(_block(CSS, CSS.index(":root{")))
_dark_at = CSS.index("@media (prefers-color-scheme:dark)")
DARK = _tokens(_block(CSS, CSS.index(":root{", _dark_at)))
COLOR_TOKENS = {k for k, v in LIGHT.items() if re.fullmatch(r"#[0-9a-fA-F]{6}", v)}
THEMES = {"light": LIGHT, "dark": {**LIGHT, **DARK}}


def rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def lum(c) -> float:
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(rgb(a)), lum(rgb(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def hue(h: str) -> float:
    return colorsys.rgb_to_hsv(*rgb(h))[0] * 360


def theme_color(theme: str, name: str) -> str:
    return THEMES[theme]["--" + name]


def component_css() -> str:
    """CSS without the two token blocks."""
    out = CSS.replace(_block(CSS, CSS.index(":root{")), "", 1)
    i = out.index(":root{", out.index("@media (prefers-color-scheme:dark)"))
    return out.replace(_block(out, i), "", 1)


# ---- 1. WCAG AA contrast, both themes
TEXT_ON = {  # foreground token -> backgrounds it is drawn on (4.5:1)
    "ink": ["paper", "sheet", "sunk", "accent-wash", "ok-wash", "warn-wash", "err-wash", "info-wash", "safety-wash"],
    "ink-2": ["paper", "sheet", "sunk"],
    "accent": ["paper", "sheet", "accent-wash", "sunk"],
    "err": ["paper", "sheet", "err-wash"],
    "ok": ["paper", "sheet", "ok-wash"],
    "warn": ["paper", "sheet", "warn-wash"],
    "info": ["paper", "sheet", "info-wash"],
    "safety": ["paper", "sheet", "safety-wash"],
    "accent-ink": ["accent"],
    "code-fg": ["code-bg"],
    "paper": ["ink"],        # toast, EN language tag
}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_contrast_aa(theme):
    bad = [(fg, bg, round(ratio(theme_color(theme, fg), theme_color(theme, bg)), 2))
           for fg, bgs in TEXT_ON.items() for bg in bgs
           if ratio(theme_color(theme, fg), theme_color(theme, bg)) < 4.5]
    assert not bad, bad


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_ui_component_and_focus_contrast(theme):
    c = lambda n: theme_color(theme, n)
    # control borders, icons and meter edges against the surfaces they sit on: 3:1
    for border in ("rule-strong",):
        for bg in ("paper", "sheet"):
            assert ratio(c(border), c(bg)) >= 3, (border, bg)
    # status glyph colours and the hazard stripe are non-text graphics: 3:1
    for fg in ("err", "ok", "warn", "info", "safety", "accent"):
        for bg in ("paper", "sheet"):
            assert ratio(c(fg), c(bg)) >= 3, (fg, bg)
    # the focus ring (accent) against every adjacent surface
    for bg in ("paper", "sheet", "sunk", "accent-wash", "ok-wash"):
        assert ratio(c("accent"), c(bg)) >= 3, ("focus", bg)
    assert re.search(r":focus-visible\{outline:[3-9]px solid var\(--accent\)", CSS)


def test_large_text_and_primary_button():
    for theme in THEMES:
        assert ratio(theme_color(theme, "accent-ink"), theme_color(theme, "accent")) >= 4.5


# ---- 2. colour is never the only carrier
STATES = ["queued", "running", "needs_review", "done", "failed", "cancelled"]


def test_status_has_text_and_shape():
    labels = re.search(r"STATE_LABEL = \{(.*?)\};", JS["common.js"], re.S).group(1)
    glyphs = {}
    for s in STATES:
        assert re.search(rf"\b{s}:\s*'[^']+'", labels), f"no text label for {s}"
        m = re.search(rf'\.st\[data-s={s}\]::before\{{content:"([^"]+)"', CSS)
        assert m, f"no shape glyph for {s}"
        glyphs[s] = m.group(1)
    assert len(set(glyphs.values())) == len(STATES), "each state needs its own glyph"
    for s in ("done", "review", "pending"):
        assert re.search(rf'\.pg\[data-s={s}\]::after\{{content:', CSS), f"page chip {s} needs a glyph"


def test_errors_safety_and_notes_have_text_or_icon():
    assert '.err-text:not(:empty)::before{content:"\\26A0' in CSS and "Error: " in CSS
    assert ".safety-note strong::before" in CSS and ".safety::before" in CSS     # icon + stripe pattern
    assert "\u26A0 Safety" in JS["review.js"]                                  # row label says Safety
    assert ".note::before" in CSS and "Note: " in CSS
    assert "Priority" in JS["portal.js"] and "\\u2605" in JS["portal.js"]       # badge: text + icon


# ---- 3. semantic colours fixed and consistent
HUES = {"err": (345, 15), "ok": (100, 170), "warn": (30, 55), "info": (215, 250)}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_semantic_hues(theme):
    for name, (lo, hi) in HUES.items():
        for tok in (name, name + "-wash"):
            h = hue(theme_color(theme, tok))
            assert (lo <= h <= hi) if lo < hi else (h >= lo or h <= hi), (tok, round(h))
    a = hue(theme_color(theme, "accent"))
    assert all(min(abs(a - hue(theme_color(theme, k))) % 360, 360 - abs(a - hue(theme_color(theme, k))) % 360) >= 25
               for k in HUES), "accent must stay clear of the semantic hues"
    s = hue(theme_color(theme, "safety"))
    assert s != hue(theme_color(theme, "err")) and theme_color(theme, "safety") != theme_color(theme, "err")


def test_one_accent_hue():
    for theme in THEMES:
        hs = [hue(theme_color(theme, k)) for k in ("accent", "accent-wash")]
        assert max(hs) - min(hs) < 15


def test_dedicated_tokens_not_used_for_decoration():
    for tok, allowed in (("safety", ("safety", "signal")), ("safety-wash", ("safety",)), ("code-bg", ("code",)),
                         ("code-fg", ("code",))):
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", component_css()):
            if f"var(--{tok})" in body:
                assert any(a in sel for a in allowed), (tok, sel.strip())


# ---- 4. tokens only
def test_no_literal_colours_in_components():
    css = component_css()
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.findall(r"\b(?:rgb|rgba|hsl|hsla)\(", css)
    for js in JS.values():
        assert not re.search(r"#[0-9a-fA-F]{6}\b", js)


def test_dark_redefines_every_colour_token_and_body_background():
    assert COLOR_TOKENS and not (COLOR_TOKENS - set(DARK)), COLOR_TOKENS - set(DARK)
    assert re.search(r"body\{[^}]*background:var\(--paper\)", CSS)
    assert re.search(r"body\{[^}]*color:var\(--ink\)", CSS)
    for h in PAGES.values():
        assert '<meta name="color-scheme" content="light dark">' in h


# ---- 5. palette, type, spacing, targets, motion
def test_two_typefaces_and_type_scale():
    assert set(re.findall(r'@font-face\{font-family:"([^"]+)"', CSS)) == {"Inter", "IBM Plex Serif"}
    css = re.sub(r"@font-face\{[^}]*\}", "", component_css())
    for fam in re.findall(r"font-family:([^;}]+)", css):
        assert fam.strip() in ("var(--font-ui)", "var(--font-doc)", "inherit"), fam
    for size in re.findall(r"font-size:([^;}]+)", css):
        assert re.fullmatch(r"var\(--t-(xs|sm|md|lg|xl|2xl)\)|[\d.]+em", size.strip()), size
    for font in re.findall(r"[{;]font:([^;}]+)", css):
        parts = font.split()
        assert parts[0] in ("inherit",) or "var(--t-" in parts[1] or re.match(r"[\d.]+r?em", parts[1]), font
    assert not re.search(r"font(-size)?:[^;}]*\d+px", css), "text sizes must be rem/em so they scale to 200%"
    assert len(set(re.findall(r"--t-[\w]+", " ".join(LIGHT)))) >= 6


def test_body_size_line_height_and_measure():
    assert LIGHT["--t-md"] == "1rem"
    body = re.search(r"body\{([^}]*)\}", CSS).group(1)
    m = re.search(r"font:400 var\(--t-md\)/([\d.]+)", body)
    assert m and 1.4 <= float(m.group(1)) <= 1.6
    for name, v in re.findall(r"([^{}]+)\{[^}]*line-height:([\d.]+)", CSS):
        pass
    for sel, ch in re.findall(r"([^{}]+)\{[^}]*max-width:(\d+)ch", CSS):
        assert int(ch) <= 75, sel


def test_spacing_scale_4_8():
    toks = {k: int(v[:-2]) for k, v in LIGHT.items() if k.startswith("--s-")}
    assert toks and all(v % 4 == 0 for v in toks.values())
    for prop, val in re.findall(r"(?:^|[{;])((?:padding|margin|gap|row-gap|column-gap)[\w-]*):([^;}]+)", component_css()):
        for px in re.findall(r"(-?\d+)px", val):
            assert int(px) % 4 == 0, (prop, val)


def test_touch_targets_and_focus():
    assert int(LIGHT["--hit"][:-2]) >= 44
    css = component_css()
    for sel in ("button,.btn", "input[type=text]", ".top nav a", ".cb", ".qrow a.id"):
        assert re.search(re.escape(sel) + r"[^{]*\{[^}]*min-(height|width):var\(--hit\)", css), sel
    assert not re.search(r"outline:\s*(none|0)", css), "never remove the focus outline"
    assert ".drop:focus-within" in css


def test_motion_tokens_and_reduced_motion():
    assert 150 <= int(LIGHT["--dur-1"][:-2]) <= 250 and 150 <= int(LIGHT["--dur-2"][:-2]) <= 250
    outside_rm = component_css().split("@media (prefers-reduced-motion:reduce)")[0]
    for t in re.findall(r"transition:([^;}]+)", outside_rm):
        assert "var(--dur-" in t, t
    m = re.search(r"@media \(prefers-reduced-motion:reduce\)\{(.*)\}\s*$", CSS, re.S)
    assert m and "animation:none" in m.group(1) and "transition:none" in m.group(1)


def test_reading_order_is_dom_order():
    css = component_css()
    assert not re.search(r"[{;]order:", css) and "-reverse" not in css


def test_component_states_exist():
    for sel in ("button:hover", "button:active", "button:disabled", ".btn:hover", ".btn:active",
                "input:disabled", ".top nav a:hover", ".drop:hover"):
        assert sel in CSS, sel
    assert ":focus-visible{" in CSS


def test_text_resize_and_zoom_allowed():
    for h in PAGES.values():
        assert "user-scalable=no" not in h and "maximum-scale" not in h
        assert 'width=device-width,initial-scale=1' in h
        assert 'lang="en"' in h or 'lang="vi"' in h


def test_no_external_resources_and_no_inline_style_or_script():
    for name, h in PAGES.items():
        assert not re.search(r'(?:src|href)="https?://', h), name
        assert not re.search(r"<script(?![^>]*(src=|type=\"application/json\"))", h), name
        assert " style=" not in h
    for name, j in JS.items():
        assert 'style="' not in j, name   # CSP forbids inline style attributes
    assert "@import" not in CSS and "url(http" not in CSS


# ---- 6. colour-blind safety
SIM = {  # linear-RGB simulation matrices (Machado et al. 2009, severity 1.0)
    "deuteranopia": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
    "protanopia": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
}


def _lin(v): return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _srgb(v):
    v = min(max(v, 0), 1)
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def simulate(h: str, kind: str):
    lin = [_lin(v) for v in rgb(h)]
    return tuple(_srgb(sum(m * x for m, x in zip(row, lin))) for row in SIM[kind])


def lab(c):
    lin = [_lin(v) for v in c]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a, b):
    return sum((p - q) ** 2 for p, q in zip(lab(a), lab(b))) ** 0.5


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("kind", list(SIM))
def test_success_error_pair_survives_colour_blindness(theme, kind):
    ok, err = theme_color(theme, "ok"), theme_color(theme, "err")
    assert delta_e(simulate(ok, kind), simulate(err, kind)) >= 20, "success and error must stay apart"


def test_success_error_also_differ_by_glyph():
    assert 'content:"\\2713"' in CSS and 'content:"\\2715"' in CSS
    # and in plain greyscale the two text colours differ in lightness by a visible margin
    for theme in THEMES:
        assert abs(lab(rgb(theme_color(theme, "ok")))[0] - lab(rgb(theme_color(theme, "err")))[0]) >= 12
