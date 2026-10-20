"""Visual theme and small animation helpers for the GUI.

tkinter's native ttk themes (vista, xpnative) delegate to Windows Visual
Styles, which ignore ``background``/``foreground``/``borderwidth`` -- that
is why a stock ttk window looks like a 2005 dialog no matter what styles
you set. To get real colour, rounded corners and smooth animation, the
interactive pieces are drawn on ``tk.Canvas`` instead.

Colours are defined once here in two palettes so the whole window can flip
between dark and light with a single switch.
"""

# ── Palettes ─────────────────────────────────────────────────────────

DARK = {
    "bg": "#0f1117",          # window backdrop
    "surface": "#171a22",     # cards
    "surface_alt": "#1e222c", # inputs / hover
    "surface_hi": "#262b38",  # pressed / active
    "border": "#2a2f3c",
    "border_hi": "#3a4152",
    "text": "#e7e9ee",
    "text_dim": "#9aa1b1",
    "text_faint": "#6b7280",
    "accent": "#6366f1",      # indigo
    "accent_hover": "#7c7ff5",
    "accent_text": "#ffffff",
    "success": "#34d399",
    "danger": "#f87171",
    "warning": "#fbbf24",
    "track": "#232833",       # progress background
    "shadow": "#080a0f",
}

LIGHT = {
    "bg": "#f4f5f9",
    "surface": "#ffffff",
    "surface_alt": "#eef0f6",
    "surface_hi": "#e2e6f0",
    "border": "#d9dde8",
    "border_hi": "#c3c9d8",
    "text": "#14161c",
    "text_dim": "#5b6376",
    "text_faint": "#8b93a5",
    "accent": "#4f46e5",
    "accent_hover": "#4338ca",
    "accent_text": "#ffffff",
    "success": "#059669",
    "danger": "#dc2626",
    "warning": "#b45309",
    "track": "#e3e6ee",
    "shadow": "#c9cede",
}

MIDNIGHT = dict(DARK, bg="#0b1020", surface="#141d33", surface_alt="#1b2740",
                surface_hi="#243451", accent="#70a5ff", accent_hover="#94bcff",
                text="#e8f0ff", text_dim="#a7b9d6", text_faint="#8196b8",
                border="#344661", border_hi="#5b789d")
CONTRAST = dict(DARK, bg="#000000", surface="#111111", surface_alt="#171717",
                surface_hi="#303030", text="#ffffff", text_dim="#ffffff",
                text_faint="#d0d0d0", border="#bbbbbb", border_hi="#ffffff",
                accent="#ffff00", accent_hover="#ffff80", accent_text="#000000",
                success="#45ff91", danger="#ff8585", warning="#ffda65")
GREEN = dict(DARK, bg="#0c1511", surface="#14221a", surface_alt="#1c2b22", border="#2c4737", accent="#4ade80", accent_hover="#86efac", accent_text="#052e16")
ORANGE = dict(DARK, bg="#18110c", surface="#261b14", surface_alt="#32241b", border="#4d3829", accent="#fb923c", accent_hover="#fdba74", accent_text="#331500")
PINK = dict(DARK, bg="#170f18", surface="#241725", surface_alt="#302032", border="#4c3050", accent="#f472b6", accent_hover="#f9a8d4", accent_text="#3d0926")
YELLOW = dict(DARK, bg="#15140c", surface="#222014", surface_alt="#2d2a1b", border="#48422a", accent="#facc15", accent_hover="#fde047", accent_text="#302400")
PALETTES = {"dark": DARK, "light": LIGHT, "midnight": MIDNIGHT,
            "green": GREEN, "orange": ORANGE, "pink": PINK, "yellow": YELLOW, "contrast": CONTRAST}
THEME_LABELS = {"dark": "Dark", "light": "Light", "midnight": "Midnight",
                "green": "Green", "orange": "Orange", "pink": "Pink", "yellow": "Yellow", "contrast": "High contrast"}

# ── Typography ───────────────────────────────────────────────────────

# Segoe UI Variable ships with Windows 11; the plain Segoe UI fallbacks keep
# this working on Windows 10 and older builds.
_DISPLAY_CANDIDATES = ("Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI")
_TEXT_CANDIDATES = ("Segoe UI Variable Text", "Segoe UI")
_MONO_CANDIDATES = ("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New")


def _pick_family(candidates, available):
    for name in candidates:
        if name in available:
            return name
    return candidates[-1]


def build_fonts(available_families, scale=100):
    """Return a dict of (family, size, weight) tuples tuned for this machine."""
    display = _pick_family(_DISPLAY_CANDIDATES, available_families)
    text = _pick_family(_TEXT_CANDIDATES, available_families)
    mono = _pick_family(_MONO_CANDIDATES, available_families)
    fonts = {
        "hero": (display, 22, "bold"),
        "title": (display, 13, "bold"),
        "subtitle": (text, 10),
        "body": (text, 10),
        "body_bold": (text, 10, "bold"),
        "small": (text, 9),
        "button": (text, 10, "bold"),
        "chip": (text, 9, "bold"),
        "entry": (text, 12),
        "mono": (mono, 9),
    }
    scale = scale if scale in (90, 100, 110, 125) else 100
    return {key: (value[0], max(8, round(value[1] * scale / 100)), *value[2:])
            for key, value in fonts.items()}


# ── Geometry ─────────────────────────────────────────────────────────

RADIUS = 10           # default corner rounding
RADIUS_SM = 7
PAD = 20              # outer padding


def lerp(a, b, t):
    """Linear interpolation between two numbers."""
    return a + (b - a) * t


def lerp_color(c1, c2, t):
    """Blend two ``#rrggbb`` strings. ``t`` of 0 returns c1, 1 returns c2."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return "#%02x%02x%02x" % (
        int(round(lerp(r1, r2, t))),
        int(round(lerp(g1, g2, t))),
        int(round(lerp(b1, b2, t))),
    )


def _hex_to_rgb(value):
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def mix_alpha(fg, bg, alpha):
    """Composite ``fg`` over ``bg`` at the given opacity."""
    return lerp_color(bg, fg, alpha)


#: Standard easing -- fast out, gentle settle. Feels responsive, not floaty.
def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = max(0.0, min(1.0, t))
    return 3 * t * t - 2 * t * t * t
