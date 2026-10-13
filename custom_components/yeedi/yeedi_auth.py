"""Custom Yeedi authenticator module for Home Assistant.

This module implements a custom authenticator for Yeedi robot vacuum cleaners.
Yeedi uses the same cloud infrastructure as Ecovacs/Deebot but with different
authentication endpoints, app keys, and org values.

The :class:`YeediAuthenticator` class implements the same interface as
:class:`deebot_client.authentication.Authenticator` and can be used as a
drop-in replacement in the deebot_client library.

Example::

    from aiohttp import ClientSession
    from yeedi_auth import YeediAuthenticator, create_yeedi_rest_config

    async with ClientSession() as session:
        config = create_yeedi_rest_config(
            session,
            device_id="your-device-id",
            alpha_2_country="DE",
        )
        authenticator = YeediAuthenticator(
            config,
            account_id="your@email.com",
            password_hash="md5-of-password",
        )
        credentials = await authenticator.authenticate()
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from aiohttp import ClientResponseError, ClientSession, ClientTimeout, hdrs

from deebot_client.const import COUNTRY_CHINA, PATH_API_USERS_USER, REALM
from deebot_client.exceptions import (
    ApiError,
    ApiTimeoutError,
    AuthenticationError,
    InvalidAuthenticationError,
)
from deebot_client.logging_filter import get_logger
from deebot_client.models import Credentials
from deebot_client.util import cancel, create_task, md5
from deebot_client.util.continents import get_continent_url_postfix
from deebot_client.util.countries import get_ecovacs_country

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

_LOGGER = get_logger(__name__)

# ---------------------------------------------------------------------------
# Yeedi-specific constants (reverse-engineered from JS library)
# ---------------------------------------------------------------------------

# Login API app key (public identifier — not a secret)
_YEEDI_CLIENT_KEY: str = "1581917520081"

# Auth code API app key (public identifier — not a secret)
_YEEDI_AUTH_CLIENT_KEY: str = "1581923437995"

# WARNING: The API client secrets (_YEEDI_CLIENT_SECRET and
# _YEEDI_AUTH_CLIENT_SECRET) were previously removed from source and
# required via the config flow.  They are actually published in the
# upstream ``ecovacs-deebot.js`` library and are NOT user-specific
# secrets, so we hardcode them here.  Node-RED's
# ``node-red-contrib-ecovacs-deebot`` does the same — its users never
# enter secrets either.

# Yeedi app metadata
_YEEDI_APP_CODE: str = "yd_global_e"
_YEEDI_APP_VERSION: str = "1.3.0"
_YEEDI_CHANNEL: str = "google_play"
_YEEDI_DEVICE_TYPE: str = "1"

# Published Yeedi app secrets (from upstream ecovacs-deebot.js
# library/constants.js — they are app-credentials, not per-user).
_YEEDI_CLIENT_SECRET: str = "ed5b3dd9a0253de7d90305d077eb5fee"
_YEEDI_AUTH_CLIENT_SECRET: str = "304a71592690995b2bb304e66b5ddee6"

# Org values used in loginByItToken
_ORG_WW: str = "ECOYDWW"
_ORG_CN: str = "ECOYDCN"

# URL paths
_USER_LOGIN_PATH_FORMAT: str = (
    "/v1/private/{country}/{lang}/{deviceId}/{appCode}"
    "/{appVersion}/{channel}/{deviceType}/user/login"
)
_GLOBAL_AUTHCODE_PATH: str = "/v1/global/auth/getAuthCode"

# Retry configuration
_MAX_RETRIES: int = 3

# HTTP timeout
_TIMEOUT: ClientTimeout = ClientTimeout(60)

# Default token validity in seconds (used when the API does not return one).
# Yeedi tokens are valid for 7 days = 604 800 s.
_DEFAULT_TOKEN_VALIDITY: int = 604800


# ---------------------------------------------------------------------------
# RestConfiguration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class RestConfiguration:
    """REST configuration for Yeedi API communication.

    Attributes:
        session: The aiohttp :class:`ClientSession` used for HTTP requests.
        device_id: A unique device identifier generated during setup.
        country: The Ecovacs/Yeedi country code (e.g., ``"DE"`` or ``"UK"``).
        portal_url: Base URL of the portal API (e.g.
            ``"https://portal-ww.ecouser.net"``).
        login_url: Base URL of the login API (e.g.
            ``"https://gl-de-api.ecovacs.yeedi.com"``).
        auth_code_url: Base URL of the auth-code API (e.g.
            ``"https://gl-de-openapi.ecovacs.yeedi.com"``).
        client_secret: API client secret for the login API.  Must be supplied
            via config flow — not hardcoded.
        auth_client_secret: API client secret for the auth-code API.  Must be
            supplied via config flow — not hardcoded.

    """

    session: ClientSession
    device_id: str
    country: str
    portal_url: str
    login_url: str
    auth_code_url: str


def create_yeedi_rest_config(
    session: ClientSession,
    *,
    device_id: str,
    alpha_2_country: str,
    override_rest_url: str | None = None,
) -> RestConfiguration:
    """Create a :class:`RestConfiguration` for Yeedi.

    The helper derives the various REST endpoints from the ISO-3166 alpha-2
    country code (e.g. ``"DE"``, ``"US"``, ``"CN"``).

    Args:
        session: An aiohttp :class:`ClientSession`.
        device_id: Unique device identifier.
        alpha_2_country: ISO-3166 alpha-2 country code.
        override_rest_url: If provided, *all* URLs (portal, login, auth-code)
            are set to this value.  Useful for local test servers / Bumper.
        client_secret: API client secret for the login API (obtained
            from config flow, not hardcoded).
        auth_client_secret: API client secret for the auth-code API
            (obtained from config flow, not hardcoded).

    Returns:
        A fully populated :class:`RestConfiguration`.

    """
    continent_postfix = get_continent_url_postfix(alpha_2_country)
    country = get_ecovacs_country(alpha_2_country)

    if override_rest_url:
        portal_url = login_url = auth_code_url = override_rest_url
    else:
        # Portal URL follows the same pattern as Ecovacs
        # e.g. "https://portal-ww.ecouser.net" or "https://portal.ecouser.net" for CN
        portal_url = f"https://portal{continent_postfix}.ecouser.net"

        country_lower = country.lower()
        # Yeedi always uses the yeedi.com TLD (except China which uses country code)
        tld = "yeedi.com" if alpha_2_country != COUNTRY_CHINA else country_lower

        login_url = f"https://gl-{country_lower}-api.ecovacs.{tld}"
        auth_code_url = f"https://gl-{country_lower}-openapi.ecovacs.{tld}"

    return RestConfiguration(
        session=session,
        device_id=device_id,
        country=country,
        portal_url=portal_url,
        login_url=login_url,
        auth_code_url=auth_code_url,
    )


# ---------------------------------------------------------------------------
# Internal auth client – handles the low-level Yeedi auth flow
# ---------------------------------------------------------------------------

class _YeediAuthClient:
    """Low-level Yeedi authentication client.

    Implements the three-step Yeedi authentication flow:

    1. **Login API** – exchange account + MD5(password) for *uid* and
       *accessToken*.
    2. **Auth Code API** – exchange *uid* + *accessToken* for a short-lived
       *authCode*.
    3. **Portal API** (``loginByItToken``) – exchange *authCode* for the
       long-lived *user_access_token* and *userId*.
    """

    def __init__(
        self,
        config: RestConfiguration,
        account_id: str,
        password_hash: str,
    ) -> None:
        self._config = config
        self._account_id = account_id
        self._password_hash = password_hash

        # Build the meta dict used in login/auth code requests.
        # Note: country and deviceId are filled per-request via **self._meta.
        self._meta: dict[str, str] = {
            "country": self._config.country.lower(),
            "lang": "EN",
            "deviceId": self._config.device_id,
            "appCode": _YEEDI_APP_CODE,
            "appVersion": _YEEDI_APP_VERSION,
            "channel": _YEEDI_CHANNEL,
            "deviceType": _YEEDI_DEVICE_TYPE,
        }

    async def login(self) -> Credentials:
        """Execute the full Yeedi login flow and return :class:`Credentials`.

        Raises:
            AuthenticationError: If any step of the flow fails.
            InvalidAuthenticationError: If the account credentials are invalid.

        """
        _LOGGER.debug("Start login to Yeedi API")

        # Step 1: Call login API → uid + accessToken
        login_password_resp = await self._call_login_api(
            self._account_id, self._password_hash
        )
        user_id = login_password_resp["uid"]

        # Step 2: Call auth code API → authCode
        auth_code = await self._call_auth_api(
            login_password_resp["accessToken"], user_id
        )

        # Step 3: Call loginByItToken via portal API → user_access_token + userId
        login_token_resp = await self._call_login_by_it_token(user_id, auth_code)
        if login_token_resp["userId"] != user_id:
            _LOGGER.debug("Switching to shorter UID")
            user_id = login_token_resp["userId"]

        user_access_token = login_token_resp["token"]

        # The 'last' field is the token validity in milliseconds.
        # Yeedi tokens are typically valid for 7 days (604 800 s).
        # We set the expiry at 99 % of the validity period to have a small
        # safety margin for the background refresh.
        validity_ms = int(login_token_resp.get("last", _DEFAULT_TOKEN_VALIDITY * 1000))
        expires_at = int(time.time() + (validity_ms / 1000) * 0.99)

        _LOGGER.debug("Login to Yeedi API successfully")
        return Credentials(
            token=user_access_token,
            user_id=user_id,
            expires_at=expires_at,
        )

    # ------------------------------------------------------------------
    # Step 1: Login API
    # ------------------------------------------------------------------

    async def _call_login_api(
        self, account_id: str, password_hash: str
    ) -> dict[str, Any]:
        """Call the Yeedi login API.

        Args:
            account_id: The user's account (e-mail or phone number).
            password_hash: MD5 hash of the user's password.

        Returns:
            The parsed ``data`` dict from the API response containing at
            least ``uid`` and ``accessToken``.

        """
        _LOGGER.debug("calling Yeedi login API")
        params: dict[str, str | int] = {
            "account": account_id,
            "password": password_hash,
            "requestId": md5(str(time.time())),
            "authTimespan": int(time.time() * 1000),
            "authTimeZone": "GMT-8",
        }

        url = urljoin(
            self._config.login_url,
            _USER_LOGIN_PATH_FORMAT.format(**self._meta),
        )

        if self._config.country == COUNTRY_CHINA:
            url += "CheckMobile"

        return await self._do_auth_response(
            url,
            self._sign(params, self._meta, _YEEDI_CLIENT_KEY, _YEEDI_CLIENT_SECRET),
        )

    # ------------------------------------------------------------------
    # Step 2: Auth Code API
    # ------------------------------------------------------------------

    async def _call_auth_api(self, access_token: str, user_id: str) -> str:
        """Call the Yeedi auth-code API.

        Args:
            access_token: The access token from the login API.
            user_id: The user ID from the login API.

        Returns:
            The ``authCode`` string.

        """
        _LOGGER.debug("calling Yeedi auth code API")
        params: dict[str, str | int] = {
            "uid": user_id,
            "accessToken": access_token,
            "bizType": "ECOVACS_IOT",
            "deviceId": self._meta["deviceId"],
            "authTimespan": int(time.time() * 1000),
        }

        url = urljoin(self._config.auth_code_url, _GLOBAL_AUTHCODE_PATH)

        response_data = await self._do_auth_response(
            url,
            self._sign(
                params, {"openId": "global"}, _YEEDI_AUTH_CLIENT_KEY, _YEEDI_AUTH_CLIENT_SECRET
            ),
        )
        return str(response_data["authCode"])

    # ------------------------------------------------------------------
    # Step 3: Portal API – loginByItToken
    # ------------------------------------------------------------------

    async def _call_login_by_it_token(
        self, user_id: str, auth_code: str
    ) -> dict[str, str]:
        """Call the portal ``loginByItToken`` endpoint.

        This call may sporadically fail with *"set token error"*; we retry
        up to three times in that case.

        Args:
            user_id: The user ID.
            auth_code: The auth code from the auth-code API.

        Returns:
            The portal response dict containing ``token``, ``userId``, …

        Raises:
            AuthenticationError: If the call fails after all retries.

        """
        org = _ORG_CN if self._config.country == COUNTRY_CHINA else _ORG_WW
        country_value = (
            "Chinese" if self._config.country == COUNTRY_CHINA else self._config.country
        )

        data: dict[str, Any] = {
            "edition": "ECOGLOBLE",
            "userId": user_id,
            "token": auth_code,
            "realm": REALM,
            "resource": self._config.device_id,
            "org": org,
            "last": "",
            "country": country_value,
            "todo": "loginByItToken",
        }

        for attempt in range(_MAX_RETRIES):
            resp = await self.post(PATH_API_USERS_USER, data)
            if resp["result"] == "ok":
                return resp  # type: ignore[return-value]
            if resp["result"] == "fail" and resp.get("error") == "set token error.":
                _LOGGER.warning(
                    "loginByItToken set token error, attempt %d/%d",
                    attempt + 2,
                    _MAX_RETRIES,
                )
                continue

            _LOGGER.error(
                "call to %s failed with %s",
                PATH_API_USERS_USER,
                resp,
            )
            msg = (
                f"failure {resp.get('error')} ({resp.get('errno')}) "
                f"for call {PATH_API_USERS_USER}"
            )
            raise AuthenticationError(msg)

        raise AuthenticationError("failed to login with token")

    # ------------------------------------------------------------------
    # Authenticated POST helper (portal API)
    # ------------------------------------------------------------------

    async def post(
        self,
        path: str,
        json: dict[str, Any],
        *,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        credentials: Credentials | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated POST request against the portal API.

        If *credentials* are provided an ``auth`` block is injected into the
        JSON payload so the portal can authenticate the request.

        Args:
            path: API path (e.g. ``"users/user.do"``).
            json: JSON payload.
            query_params: Optional URL query parameters.
            headers: Optional extra HTTP headers.
            credentials: When given, authentication info is added to *json*.

        Returns:
            The parsed JSON response body.

        Raises:
            ApiError: On HTTP or API errors (including 502 Bad Gateway after
                retries).
            ApiTimeoutError: When the request times out.

        """
        url = urljoin(self._config.portal_url, "api/" + path)
        logger_request_params = f"url={url}, params={query_params}, json={json}"

        if credentials is not None:
            json = {
                **json,
                "auth": {
                    "with": "users",
                    "userid": credentials.user_id,
                    "realm": REALM,
                    "token": credentials.token,
                    "resource": self._config.device_id,
                },
            }

        for attempt in range(_MAX_RETRIES):
            _LOGGER.debug(
                "Calling Yeedi api (%d/%d): %s",
                attempt + 1,
                _MAX_RETRIES,
                logger_request_params,
            )

            try:
                async with self._config.session.post(
                    url,
                    json=json,
                    params=query_params,
                    headers=headers,
                    timeout=_TIMEOUT,
                ) as res:
                    if res.status == HTTPStatus.OK:
                        response_data: dict[str, Any] = await res.json()
                        _LOGGER.debug(
                            "Success calling Yeedi api %s, response=%s",
                            logger_request_params,
                            response_data,
                        )
                        return response_data

                    _LOGGER.debug(
                        "Error calling Yeedi api %s, response=%s",
                        logger_request_params,
                        res,
                    )
                    raise ClientResponseError(
                        res.request_info,
                        res.history,
                        status=res.status,
                        message=str(res.reason),
                        headers=res.headers,
                    )
            except TimeoutError as ex:
                _LOGGER.debug(
                    "Timeout (%s) reached on path: %s",
                    _TIMEOUT,
                    path,
                )
                raise ApiTimeoutError(path=path, timeout=_TIMEOUT) from ex
            except ClientResponseError as ex:
                _LOGGER.debug(
                    "Error: %s",
                    logger_request_params,
                    exc_info=True,
                )
                if ex.status == HTTPStatus.BAD_GATEWAY:
                    seconds_to_sleep = 10
                    _LOGGER.info(
                        "Retry calling Yeedi API due 502: "
                        "Unfortunately the Yeedi API is unreliable. "
                        "Retrying in %d seconds",
                        seconds_to_sleep,
                    )
                    await asyncio.sleep(seconds_to_sleep)
                    continue

                raise ApiError from ex

        raise ApiError("Unknown error occurred")

    # ------------------------------------------------------------------
    # Signing helper
    # ------------------------------------------------------------------

    @staticmethod
    def _sign(
        params: dict[str, str | int],
        additional_sign_params: Mapping[str, str | int],
        key: str,
        secret: str,
    ) -> dict[str, str | int]:
        """Sign request parameters using the Yeedi/Ecovacs algorithm.

        1. Merge *additional_sign_params* into *params*.
        2. Build a string: ``key`` + sorted ``key=value`` pairs + ``secret``.
        3. MD5-hash the string.
        4. Add ``authAppkey`` (set to *key*) and ``authSign`` (set to the
           MD5 hash) to the original *params* dict and return it.

        Args:
            params: Request parameters to sign.
            additional_sign_params: Extra parameters included in the signature
                but not added to the returned dict (e.g. meta fields).
            key: The app key.
            secret: The app secret.

        Returns:
            The *params* dict with ``authAppkey`` and ``authSign`` added.

        """
        sign_data: dict[str, str | int] = {**additional_sign_params, **params}
        sign_on_text = (
            key
            + "".join(
                [k + "=" + str(sign_data[k]) for k in sorted(sign_data.keys())]
            )
            + secret
        )
        params["authSign"] = md5(sign_on_text)
        params["authAppkey"] = key
        return params

    # ------------------------------------------------------------------
    # Low-level auth response handler
    # ------------------------------------------------------------------

    async def _do_auth_response(
        self, url: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a signed GET request and parse the Yeedi auth response.

        Yeedi/Ecovacs auth endpoints return JSON with a ``code`` field:

        - ``"0000"`` → success, return the ``data`` dict.
        - ``"1005"`` or ``"1010"`` → invalid credentials.
        - anything else → generic authentication error.

        Args:
            url: Full request URL.
            params: Signed request parameters.

        Returns:
            The ``data`` dict from a successful response.

        Raises:
            InvalidAuthenticationError: For error codes ``"1005"`` / ``"1010"``.
            AuthenticationError: For any other error code.

        """
        async with self._config.session.get(
            url, params=params, timeout=_TIMEOUT
        ) as res:
            res.raise_for_status()

            # Ecovacs/Yeedi return JSON but set Content-Type to text/plain.
            content_type = res.headers.get(hdrs.CONTENT_TYPE, "").lower()
            json = await res.json(content_type=content_type)
            _LOGGER.debug("got %s", json)

            if json["code"] == "0000":
                data: dict[str, Any] = json["data"]
                return data
            if json["code"] in ("1005", "1010"):
                raise InvalidAuthenticationError

            _LOGGER.error("call to %s failed with %s", url, json)
            msg = f"failure code {json['code']} ({json.get('msg', '')}) for call {url}"
            raise AuthenticationError(msg)


# ---------------------------------------------------------------------------
# YeediAuthenticator – public authenticator matching deebot_client interface
# ---------------------------------------------------------------------------

class YeediAuthenticator:
    """Yeedi authenticator compatible with :class:`deebot_client.authentication.Authenticator`.

    This class wraps :class:`_YeediAuthClient` and adds:

    - **Credential caching** – repeated calls to :meth:`authenticate` return
      cached credentials until they expire.
    - **Automatic token refresh** – a background task refreshes the token
      shortly before it expires.
    - **Change subscriptions** – callbacks are fired whenever new credentials
      are obtained.
    - **Thread safety** – an :class:`asyncio.Lock` prevents concurrent logins.

    The public API mirrors the Ecovacs ``Authenticator`` exactly so it can be
    used as a drop-in replacement in the deebot client stack.

    Args:
        config: A :class:`RestConfiguration` created via
            :func:`create_yeedi_rest_config`.
        account_id: The user's account identifier (e-mail or phone number).
        password_hash: The MD5 hash of the user's password.

    """

    def __init__(
        self,
        config: RestConfiguration,
        account_id: str,
        password_hash: str,
    ) -> None:
        self._auth_client = _YeediAuthClient(
            config,
            account_id,
            password_hash,
        )

        self._lock = asyncio.Lock()
        self._on_credentials_changed: set[
            Callable[[Credentials], Coroutine[Any, Any, None]]
        ] = set()
        self._credentials: Credentials | None = None
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Future[Any]] = set()

    async def authenticate(self, *, force: bool = False) -> Credentials:
        """Authenticate on Yeedi servers and return valid credentials.

        If valid cached credentials exist and *force* is ``False``, the cached
        instance is returned immediately.  Otherwise a full login flow is
        executed, subscribers are notified, and a background refresh task is
        scheduled.

        Args:
            force: When ``True``, ignore any cached credentials and perform a
                fresh login.

        Returns:
            Valid :class:`Credentials`.

        """
        async with self._lock:
            if (
                self._credentials is None
                or force
                or self._credentials.expires_at < time.time()
            ):
                _LOGGER.debug("Performing Yeedi login")
                self._credentials = await self._auth_client.login()
                self._cancel_refresh_task()
                self._create_refresh_task(self._credentials)

                for on_changed in self._on_credentials_changed:
                    create_task(self._tasks, on_changed(self._credentials))

            return self._credentials

    def subscribe(
        self, callback: Callable[[Credentials], Coroutine[Any, Any, None]]
    ) -> Callable[[], None]:
        """Register a callback that is invoked when credentials change.

        The callback receives the new :class:`Credentials` instance whenever
        a fresh login is performed.

        Args:
            callback: An async callable accepting :class:`Credentials`.

        Returns:
            A no-argument callable that unsubscribes the callback when invoked.

        """

        def unsubscribe() -> None:
            self._on_credentials_changed.remove(callback)

        self._on_credentials_changed.add(callback)
        return unsubscribe

    async def post_authenticated(
        self,
        path: str,
        json: dict[str, Any],
        *,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        credentials: Credentials | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated POST request against the Yeedi portal API.

        If *credentials* are not provided, the method automatically calls
        :meth:`authenticate` to obtain current ones.

        Args:
            path: API path (e.g. ``"users/user.do"``).
            json: JSON payload.
            query_params: Optional URL query parameters.
            headers: Optional extra HTTP headers.
            credentials: Optional explicit credentials to use.  When *None*
                the cached credentials are used.

        Returns:
            The parsed JSON response body.

        """
        return await self._auth_client.post(
            path,
            json,
            query_params=query_params,
            headers=headers,
            credentials=credentials or await self.authenticate(),
        )

    async def teardown(self) -> None:
        """Release all resources held by the authenticator.

        Cancels the pending background refresh task and waits for any
        in-flight subscriber tasks to finish.

        """
        self._cancel_refresh_task()
        await cancel(self._tasks)

    def _cancel_refresh_task(self) -> None:
        """Cancel the scheduled token-refresh timer if it exists."""
        if self._refresh_handle and not self._refresh_handle.cancelled():
            self._refresh_handle.cancel()

    def _create_refresh_task(self, credentials: Credentials) -> None:
        """Schedule a background refresh shortly before the token expires.

        The refresh is scheduled at 99 % of the remaining validity period so
        that there is always a valid credential available.

        Args:
            credentials: The newly obtained credentials.

        """

        def refresh() -> None:
            _LOGGER.debug("Refresh Yeedi token")

            async def async_refresh() -> None:
                try:
                    await self.authenticate(force=True)
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception(
                        "An exception occurred during refreshing Yeedi token"
                    )

            create_task(self._tasks, async_refresh())
            self._refresh_handle = None

        validity = (credentials.expires_at - time.time()) * 0.99

        self._refresh_handle = asyncio.get_event_loop().call_later(
            validity, refresh
        )
