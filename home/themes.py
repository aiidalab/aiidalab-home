class IconSetDefault:
    # General purpose icons
    CHECK = '<i class="fa fa-check-circle" aria-hidden="true"></i>'
    WARNING = '<i class="fa fa-exclamation-circle" aria-hidden="true"></i>'
    TIMES_CIRCLE = '<i class="fa fa-times-circle"></i>'
    BLOCKED = '<i class="fa fa-minus-circle" aria-hidden="true"></i>'
    CHAIN_BROKEN = '<i class="fa fa-chain-broken" aria-hidden="true"></i>'
    LOADING = '<i class="fa fa-hourglass" aria-hidden="true"></i>'
    ARROW_CIRCLE_UP = '<i class="fa fa-arrow-circle-up" aria-hidden="true"></i>'
    FOLDER = '<i class="fa fa-folder-o" aria-hidden="true"></i>'

    # App states (general)
    APP_DETACHED = CHAIN_BROKEN
    APP_INCOMPATIBLE = TIMES_CIRCLE
    APP_VERSION_INCOMPATIBLE = WARNING
    APP_NOT_REGISTERED = FOLDER

    # App states (updates)
    APP_NO_UPDATE_AVAILABLE = CHECK
    APP_UPDATE_AVAILABLE = ARROW_CIRCLE_UP
    APP_UPDATE_AVAILABLE_UNKNOWN = TIMES_CIRCLE


class ColorMapDefault:
    GRAY = "#666666"
    RED = "#CC0000"
    YELLOW = "#FFCC00"
    GREEN = "#009900"
    LIGHT_GREEN = "#33CC00"
    LIGHT_BLUE = "#6666FF"

    AIIDALAB_BLUE = "#018DDA"
    AIIDALAB_GREEN = "#28B104"
    AIIDALAB_ORANGE = "#FF7211"

    CHECK = AIIDALAB_GREEN
    NOTIFY = AIIDALAB_BLUE
    WARNING = YELLOW
    DANGER = RED


class ThemeDefault:
    ICONS = IconSetDefault
    COLORS = ColorMapDefault
