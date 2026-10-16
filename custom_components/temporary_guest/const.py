"""Constants for the Temporary Guest integration."""

from typing import Final

DOMAIN: Final = "temporary_guest"
NAME: Final = "Temporary Guest"
DATA_MANAGER: Final = "manager"

CONF_USERNAME: Final = "username"
CONF_DISPLAY_NAME: Final = "display_name"
CONF_LOCAL_ONLY: Final = "local_only"
CONF_AUTO_ROTATE: Final = "auto_rotate"
CONF_ROTATION_TIME: Final = "rotation_time"
CONF_PASSWORD_LENGTH: Final = "password_length"
CONF_READ_ENTITIES: Final = "read_entities"
CONF_CONTROL_ENTITIES: Final = "control_entities"
CONF_GUEST_DASHBOARD_PATH: Final = "guest_dashboard_path"

DEFAULT_USERNAME: Final = "mzuser"
DEFAULT_DISPLAY_NAME: Final = "临时访客"
DEFAULT_LOCAL_ONLY: Final = False
DEFAULT_AUTO_ROTATE: Final = True
DEFAULT_ROTATION_TIME: Final = "08:00:00"
DEFAULT_PASSWORD_LENGTH: Final = 24
DEFAULT_READ_ENTITIES: Final[tuple[str, ...]] = ()
DEFAULT_CONTROL_ENTITIES: Final[tuple[str, ...]] = ()
DEFAULT_GUEST_DASHBOARD_PATH: Final = "/dashboard-mzuser"
MIN_PASSWORD_LENGTH: Final = 16
MAX_PASSWORD_LENGTH: Final = 64

RESTRICTED_GROUP_ID: Final = "temporary_guest_restricted"
RESTRICTED_GROUP_NAME: Final = "Temporary Guest restricted entities"

CARD_URL: Final = "/temporary_guest/temporary-guest-card.js"
CARD_FILENAME: Final = "temporary-guest-card.js"

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = "temporary_guest"

WS_STATUS: Final = "temporary_guest/status"
WS_REVEAL: Final = "temporary_guest/reveal"
WS_ROTATE: Final = "temporary_guest/rotate"
WS_SET_ACTIVE: Final = "temporary_guest/set_active"
WS_SET_AUTO_ROTATE: Final = "temporary_guest/set_auto_rotate"
WS_UI_POLICY: Final = "temporary_guest/ui_policy"
