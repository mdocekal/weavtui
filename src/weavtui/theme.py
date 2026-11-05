"""Colour themes: Weaviate logo greens on the terminal's own background.

No background colour is painted; background, surfaces and text use the terminal's
defaults (``ansi_default``). Only highlights use Weaviate's brand colours, taken from the
Weaviate logo and docs.weaviate.io. Use "weaviate-light" on light terminal backgrounds;
switch with Ctrl+P -> "Change theme".
"""

from textual.theme import Theme

GREEN = "#75BE2C"  # logo green
GREEN_DARK = "#348522"
PINK = "#FA0171"

_TERMINAL = {
    "foreground": "ansi_default",
    "background": "ansi_default",
    "surface": "ansi_default",
    "panel": "ansi_default",
    "boost": "ansi_default",
}

WEAVIATE_DARK = Theme(
    name="weaviate",
    ansi=True,
    primary=GREEN,
    secondary=GREEN_DARK,
    accent=PINK,
    success=GREEN,
    warning="ansi_yellow",
    error="ansi_red",
    dark=True,
    **_TERMINAL,
    variables={
        "ansi-background": "ansi_black",
        "ansi-foreground": "ansi_white",
        "border": GREEN,
        "border-blurred": "ansi_bright_black",
        "block-cursor-foreground": "ansi_black",
        "block-cursor-background": GREEN,
        "block-cursor-blurred-foreground": "ansi_default",
        "block-cursor-blurred-background": GREEN_DARK,
        "block-hover-background": "ansi_default",
        "input-cursor-background": GREEN,
        "input-cursor-foreground": "ansi_black",
        "input-selection-background": GREEN_DARK,
        "input-selection-foreground": "ansi_bright_white",
        "screen-selection-background": GREEN_DARK,
        "screen-selection-foreground": "ansi_bright_white",
        "scrollbar": GREEN_DARK,
        "scrollbar-hover": GREEN,
        "scrollbar-active": GREEN,
        "scrollbar-background": "ansi_default",
        "scrollbar-background-hover": "ansi_default",
        "scrollbar-background-active": "ansi_default",
        "scrollbar-corner-color": "ansi_default",
        "footer-key-foreground": GREEN,
        "footer-background": "ansi_default",
        "footer-item-background": "ansi_default",
        "footer-description-background": "ansi_default",
        "footer-key-background": "ansi_default",
    },
)

WEAVIATE_LIGHT = Theme(
    name="weaviate-light",
    ansi=True,
    primary=GREEN_DARK,
    secondary=GREEN,
    accent=PINK,
    success=GREEN_DARK,
    warning="ansi_yellow",
    error="ansi_red",
    dark=False,
    **_TERMINAL,
    variables={
        "ansi-background": "ansi_white",
        "ansi-foreground": "ansi_black",
        "border": GREEN_DARK,
        "border-blurred": "ansi_white",
        "block-cursor-foreground": "ansi_bright_white",
        "block-cursor-background": GREEN_DARK,
        "block-cursor-blurred-foreground": "ansi_default",
        "block-cursor-blurred-background": GREEN,
        "block-hover-background": "ansi_default",
        "input-cursor-background": GREEN_DARK,
        "input-cursor-foreground": "ansi_bright_white",
        "input-selection-background": GREEN,
        "input-selection-foreground": "ansi_black",
        "screen-selection-background": GREEN,
        "screen-selection-foreground": "ansi_black",
        "scrollbar": GREEN,
        "scrollbar-hover": GREEN_DARK,
        "scrollbar-active": GREEN_DARK,
        "scrollbar-background": "ansi_default",
        "scrollbar-background-hover": "ansi_default",
        "scrollbar-background-active": "ansi_default",
        "scrollbar-corner-color": "ansi_default",
        "footer-key-foreground": GREEN_DARK,
        "footer-background": "ansi_default",
        "footer-item-background": "ansi_default",
        "footer-description-background": "ansi_default",
        "footer-key-background": "ansi_default",
    },
)

THEMES = (WEAVIATE_DARK, WEAVIATE_LIGHT)
DEFAULT_THEME = WEAVIATE_DARK.name
