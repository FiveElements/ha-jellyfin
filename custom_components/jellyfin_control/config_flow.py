"""Config flow for the Jellyfin Control integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

import probatio

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.uuid import random_uuid_hex

from . import create_client
from .api import (
    AuthResult,
    JellyfinAuthError,
    JellyfinClient,
    JellyfinError,
    JellyfinNotFoundError,
    PublicInfo,
    QuickConnectRequest,
    normalize_url,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_AUDIO_CODEC,
    CONF_CLIENT_DEVICE_ID,
    CONF_SERVER_ID,
    CONF_USER_ID,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OPTION_SCAN_INTERVAL,
    SUPPORTED_AUDIO_CODECS,
)
from .coordinator import JellyfinConfigEntry

SERVER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        probatio.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)

PASSWORD_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_USERNAME): str,
        probatio.Optional(probatio.Secret(CONF_PASSWORD), default=""): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

AUTH_MENU = ["password", "quick_connect"]


class JellyfinControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Jellyfin server: address first, then password or Quick Connect."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._url = ""
        self._verify_ssl = True
        self._device_id = random_uuid_hex()
        self._server: PublicInfo | None = None
        self._quick_connect: QuickConnectRequest | None = None

    def _client(self) -> JellyfinClient:
        return create_client(
            self.hass, self._url, self._device_id, verify_ssl=self._verify_ssl
        )

    async def _locate(self, url: str, verify_ssl: bool) -> str | None:
        """Find the server; return an error key or None."""
        self._url = normalize_url(url)
        self._verify_ssl = verify_ssl
        client = self._client()
        try:
            self._server = await client.locate_server()
        except JellyfinError:
            return "cannot_connect"
        except Exception:
            LOGGER.exception("Unexpected error while contacting Jellyfin")
            return "unknown"
        # The base path found behind a reverse proxy is part of the URL.
        self._url = client.url
        return None

    # -- create -------------------------------------------------------------

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._locate(
                user_input[CONF_URL], user_input.get(CONF_VERIFY_SSL, True)
            )
            if error is None:
                assert self._server is not None
                await self.async_set_unique_id(self._server.server_id)
                self._abort_if_unique_id_configured(
                    updates={CONF_URL: self._url, CONF_VERIFY_SSL: self._verify_ssl}
                )
                return await self.async_step_auth()
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SERVER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to sign in."""
        return self.async_show_menu(
            step_id="auth",
            menu_options=AUTH_MENU,
            description_placeholders={"server": self._server_name()},
        )

    def _server_name(self) -> str:
        return self._server.name if self._server else self._url

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in with a username and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                auth = await self._client().authenticate(
                    user_input[CONF_USERNAME], user_input.get(CONF_PASSWORD, "")
                )
            except JellyfinAuthError:
                errors["base"] = "invalid_auth"
            except JellyfinError:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error while signing in")
                errors["base"] = "unknown"
            else:
                return await self._async_finish(auth)
        suggested = {CONF_USERNAME: (user_input or {}).get(CONF_USERNAME)}
        return self.async_show_form(
            step_id="password",
            data_schema=self.add_suggested_values_to_schema(PASSWORD_SCHEMA, suggested),
            errors=errors,
            description_placeholders={"server": self._server_name()},
        )

    async def async_step_quick_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in by approving a code from a signed-in Jellyfin app."""
        client = self._client()
        errors: dict[str, str] = {}
        try:
            if self._quick_connect is None:
                if not await client.quick_connect_enabled():
                    return self.async_abort(reason="quick_connect_disabled")
                self._quick_connect = await client.quick_connect_initiate()
            elif user_input is not None:
                try:
                    state = await client.quick_connect_state(self._quick_connect.secret)
                except JellyfinNotFoundError:
                    # Requests expire after a few minutes: start a new one.
                    self._quick_connect = await client.quick_connect_initiate()
                    errors["base"] = "quick_connect_expired"
                else:
                    if state.authenticated:
                        auth = await client.authenticate_quick_connect(state.secret)
                        self._quick_connect = None
                        return await self._async_finish(auth)
                    errors["base"] = "quick_connect_pending"
        except JellyfinAuthError:
            self._quick_connect = None
            errors["base"] = "invalid_auth"
        except JellyfinError:
            return self.async_abort(reason="cannot_connect")
        code = self._quick_connect.code if self._quick_connect else ""
        return self.async_show_form(
            step_id="quick_connect",
            data_schema=probatio.Schema({}),
            errors=errors,
            description_placeholders={"code": code, "server": self._server_name()},
        )

    async def _async_finish(self, auth: AuthResult) -> ConfigFlowResult:
        assert self._server is not None
        data = {
            CONF_URL: self._url,
            CONF_VERIFY_SSL: self._verify_ssl,
            CONF_CLIENT_DEVICE_ID: self._device_id,
            CONF_ACCESS_TOKEN: auth.access_token,
            CONF_USER_ID: auth.user_id,
            CONF_SERVER_ID: self._server.server_id,
        }
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if auth.user_id != entry.data[CONF_USER_ID]:
                # Signing in as someone else would silently hand the entry
                # (sessions, library, played state) to another account.
                await self._async_revoke(auth)
                return self.async_abort(reason="wrong_account")
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    **data,
                    CONF_CLIENT_DEVICE_ID: entry.data[CONF_CLIENT_DEVICE_ID],
                },
            )
        return self.async_create_entry(title=self._server.name, data=data)

    async def _async_revoke(self, auth: AuthResult) -> None:
        """Log a token out, ignoring a server that is gone."""
        client = create_client(
            self.hass,
            self._url,
            self._device_id,
            verify_ssl=self._verify_ssl,
            access_token=auth.access_token,
            user_id=auth.user_id,
        )
        try:
            await client.logout()
        except JellyfinError as err:
            LOGGER.debug("Could not revoke the unused token: %s", err)

    # -- reauth -------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Sign in again after the token was revoked."""
        self._url = entry_data[CONF_URL]
        self._verify_ssl = entry_data.get(CONF_VERIFY_SSL, True)
        self._device_id = entry_data[CONF_CLIENT_DEVICE_ID]
        try:
            self._server = await self._client().public_info()
        except JellyfinError:
            return self.async_abort(reason="cannot_connect")
        if self._server.server_id != entry_data.get(CONF_SERVER_ID):
            return self.async_abort(reason="unique_id_mismatch")
        return await self.async_step_auth()

    # -- reconfigure ----------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the server address of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            self._device_id = entry.data[CONF_CLIENT_DEVICE_ID]
            error = await self._locate(
                user_input[CONF_URL], user_input.get(CONF_VERIFY_SSL, True)
            )
            if error is None:
                assert self._server is not None
                await self.async_set_unique_id(self._server.server_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: self._url,
                        CONF_VERIFY_SSL: self._verify_ssl,
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                SERVER_SCHEMA, user_input or dict(entry.data)
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: JellyfinConfigEntry,
    ) -> JellyfinOptionsFlow:
        """Create the options flow."""
        return JellyfinOptionsFlow()


class JellyfinOptionsFlow(OptionsFlowWithReload):
    """Polling interval and streaming codec."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            user_input[OPTION_SCAN_INTERVAL] = int(user_input[OPTION_SCAN_INTERVAL])
            return self.async_create_entry(data=user_input)
        schema = probatio.Schema(
            {
                probatio.Required(
                    OPTION_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                probatio.Required(
                    CONF_AUDIO_CODEC, default=DEFAULT_AUDIO_CODEC
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=SUPPORTED_AUDIO_CODECS,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_AUDIO_CODEC,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )
