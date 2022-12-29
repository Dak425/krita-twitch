from krita import (Document, Krita, Window, View, ManagedColor)

# Action identifiers
ACTION_TOGGLE_FG_BG = 'toggle_fg_bg'

# So I don't have to write 'Krita.instance()' twenty thousand times
KI = Krita.instance()

def get_window() -> Window:
   return KI.activeWindow()

def get_view() -> View:
    return get_window().activeView()

def get_document() -> Document:
    return get_view().document()

# Color Related Controls
def switch_colors():
    return KI.action(ACTION_TOGGLE_FG_BG).trigger()
    
def set_bg_color(color: ManagedColor) -> None:
    return get_view().setBackGroundColor(color)
    
def set_fg_color(color: ManagedColor) -> None:
    return get_view().setForeGroundColor(color)
    
# Brush Related Controls
def set_brush_size(size: int) -> None:
    return get_view().setBrushSize(size)