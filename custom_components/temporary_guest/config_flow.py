"""Config flow for Temporary Guest."""

from __future__ import annotations

from typing import Any, override

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    TextSelector,
    TimeSelector,
)

from .const import (
    CONF_AUTO_ROTATE,
    CONF_CONTROL_ENTITIES,
    CONF_DISPLAY_NAME,
    CONF_GUEST_DASHBOARD_PATH,
    CONF_LOCAL_ONLY,
    CONF_PASSWORD_LENGTH,
    CONF_READ_ENTITIES,
    CONF_ROTATION_TIME,
    CONF_USERNAME,
    DEFAULT_AUTO_ROTATE,
    DEFAULT_CONTROL_ENTITIES,
    DEFAULT_DISPLAY_NAME,
    DEFAULT_GUEST_DASHBOARD_PATH,
    DEFAULT_LOCAL_ONLY,
    DEFAULT_PASSWORD_LENGTH,
    DEFAULT_READ_ENTITIES,
    DEFAULT_ROTATION_TIME,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
)
from .dashboard import normalize_dashboard_path


async def _async_username_exists(hass, username: str) -> bool:
    """Return whether a linked Home Assistant credential uses this username."""
    for user in await hass.auth.async_get_users():
        for credential in user.credentials:
            if (
                credential.auth_provider_type == "homeassistant"
                and str(credential.data.get("username", "")).strip().casefold()
                == username
            ):
                return True
    return False


class TemporaryGuestConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Temporary Guest setup flow."""

    VERSION = 1

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return TemporaryGuestOptionsFlow(config_entry)

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create one dedicated non-admin guest account."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip().casefold()
            try:
                dashboard_path = normalize_dashboard_path(
                    user_input[CONF_GUEST_DASHBOARD_PATH]
                )
            except ValueError:
                errors[CONF_GUEST_DASHBOARD_PATH] = "invalid_dashboard_path"
            if not username:
                errors[CONF_USERNAME] = "invalid_username"
            elif await _async_username_exists(self.hass, username):
                errors[CONF_USERNAME] = "username_exists"
            if not errors:
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_configured()
                password_length = int(user_input[CONF_PASSWORD_LENGTH])
                data = {
                    CONF_USERNAME: username,
                    CONF_DISPLAY_NAME: str(user_input[CONF_DISPLAY_NAME]).strip()
                    or DEFAULT_DISPLAY_NAME,
                    CONF_LOCAL_ONLY: bool(user_input[CONF_LOCAL_ONLY]),
                }
                options = {
                    CONF_AUTO_ROTATE: bool(user_input[CONF_AUTO_ROTATE]),
                    CONF_ROTATION_TIME: str(user_input[CONF_ROTATION_TIME]),
                    CONF_PASSWORD_LENGTH: password_length,
                    CONF_GUEST_DASHBOARD_PATH: dashboard_path,
                    **_entity_permission_options(user_input),
                }
                return self.async_create_entry(
                    title=data[CONF_DISPLAY_NAME], data=data, options=options
                )

        schema = probatio.Schema(
            {
                probatio.Required(CONF_USERNAME, default=DEFAULT_USERNAME): TextSelector(),
                probatio.Required(
                    CONF_DISPLAY_NAME, default=DEFAULT_DISPLAY_NAME
                ): TextSelector(),
                probatio.Required(
                    CONF_LOCAL_ONLY, default=DEFAULT_LOCAL_ONLY
                ): BooleanSelector(),
                probatio.Required(
                    CONF_AUTO_ROTATE, default=DEFAULT_AUTO_ROTATE
                ): BooleanSelector(),
                probatio.Required(
                    CONF_ROTATION_TIME, default=DEFAULT_ROTATION_TIME
                ): TimeSelector(),
                probatio.Required(
                    CONF_PASSWORD_LENGTH, default=DEFAULT_PASSWORD_LENGTH
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_PASSWORD_LENGTH,
                        max=MAX_PASSWORD_LENGTH,
                        step=1,
                    )
                ),
                probatio.Required(
                    CONF_GUEST_DASHBOARD_PATH,
                    default=DEFAULT_GUEST_DASHBOARD_PATH,
                ): TextSelector(),
                probatio.Required(
                    CONF_READ_ENTITIES, default=list(DEFAULT_READ_ENTITIES)
                ): EntitySelector(EntitySelectorConfig(multiple=True)),
                probatio.Required(
                    CONF_CONTROL_ENTITIES, default=list(DEFAULT_CONTROL_ENTITIES)
                ): EntitySelector(EntitySelectorConfig(multiple=True)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class TemporaryGuestOptionsFlow(OptionsFlow):
    """Allow changing rotation settings."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._entry = config_entry

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the schedule and generated password length."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                dashboard_path = normalize_dashboard_path(
                    user_input[CONF_GUEST_DASHBOARD_PATH]
                )
            except ValueError:
                errors[CONF_GUEST_DASHBOARD_PATH] = "invalid_dashboard_path"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_AUTO_ROTATE: bool(user_input[CONF_AUTO_ROTATE]),
                        CONF_ROTATION_TIME: str(user_input[CONF_ROTATION_TIME]),
                        CONF_PASSWORD_LENGTH: int(user_input[CONF_PASSWORD_LENGTH]),
                        CONF_GUEST_DASHBOARD_PATH: dashboard_path,
                        **_entity_permission_options(user_input),
                    },
                )

        options = self._entry.options
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_AUTO_ROTATE,
                    default=options.get(CONF_AUTO_ROTATE, DEFAULT_AUTO_ROTATE),
                ): BooleanSelector(),
                probatio.Required(
                    CONF_ROTATION_TIME,
                    default=options.get(CONF_ROTATION_TIME, DEFAULT_ROTATION_TIME),
                ): TimeSelector(),
                probatio.Required(
                    CONF_PASSWORD_LENGTH,
                    default=options.get(CONF_PASSWORD_LENGTH, DEFAULT_PASSWORD_LENGTH),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_PASSWORD_LENGTH,
                        max=MAX_PASSWORD_LENGTH,
                        step=1,
                    )
                ),
                probatio.Required(
                    CONF_GUEST_DASHBOARD_PATH,
                    default=(
                        user_input[CONF_GUEST_DASHBOARD_PATH]
                        if user_input is not None
                        else options.get(
                            CONF_GUEST_DASHBOARD_PATH,
                            DEFAULT_GUEST_DASHBOARD_PATH,
                        )
                    ),
                ): TextSelector(),
                probatio.Required(
                    CONF_READ_ENTITIES,
                    default=options.get(CONF_READ_ENTITIES, list(DEFAULT_READ_ENTITIES)),
                ): EntitySelector(EntitySelectorConfig(multiple=True)),
                probatio.Required(
                    CONF_CONTROL_ENTITIES,
                    default=options.get(
                        CONF_CONTROL_ENTITIES, list(DEFAULT_CONTROL_ENTITIES)
                    ),
                ): EntitySelector(EntitySelectorConfig(multiple=True)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


def _entity_permission_options(user_input: dict[str, Any]) -> dict[str, list[str]]:
    """Normalize selector values and let controllable entities win overlaps."""
    control = list(dict.fromkeys(user_input[CONF_CONTROL_ENTITIES]))
    control_set = set(control)
    read = [
        entity_id
        for entity_id in dict.fromkeys(user_input[CONF_READ_ENTITIES])
        if entity_id not in control_set
    ]
    return {CONF_READ_ENTITIES: read, CONF_CONTROL_ENTITIES: control}
