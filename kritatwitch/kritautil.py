from krita import Krita, ManagedColor

# Action identifiers
ACTION_TOGGLE_FG_BG = 'toggle_fg_bg'

def get_window():
   return Krita.instance().activeWindow()

def get_view():
    return get_window().activeView()

def get_document():
    return get_view().document()

# Color Related Controls
def switch_colors():
    return Krita.instance().action(ACTION_TOGGLE_FG_BG).trigger()
    
def set_bg_color(red: int, green: int, blue: int):
    color = _rgb_to_managed_color(red, green, blue)
    return get_view().setBackGroundColor(color)
    
def set_fg_color(red: int, green: int, blue: int):
    color = _rgb_to_managed_color(red, green, blue)
    return get_view().setForeGroundColor(color)
    
# Brush Related Controls
def set_brush_size(size: int):
    return get_view().setBrushSize(size)

def _rgb_to_managed_color(red: int, green: int, blue: int) -> ManagedColor:
    color = ManagedColor("RGBA", "U8", "")
    components = color.components()
    components[0] = blue
    components[1] = green
    components[2] = red
    components[3] = 1
    color.setComponents(components)
    return color