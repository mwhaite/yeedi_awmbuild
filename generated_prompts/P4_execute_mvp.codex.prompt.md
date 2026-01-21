# P4 — Execute MVP: Home Assistant Yeedi Integration

You are coding a Home Assistant custom integration.

## Inputs (authoritative)
- /project_state/project_spec.yaml
- /project_state/mvp_contract.yaml
- /project_state/execution_shape.yaml
- /project_state/stack_lock.yaml

## Mission
Implement the MVP integration under `custom_components/yeedi`.
The integration must authenticate to the Yeedi cloud using a Yeedi-specific flow.
A Node-RED flow/script from the Home Assistant forums is an important reference for required headers, endpoints, and token fields; design the client so these details are isolated in `api.py`.

## Hard requirements
- Use Home Assistant async patterns.
- Use `aiohttp` from Home Assistant (no requests).
- Use `ConfigFlow` for UI setup and `OptionsFlow` if a region/base-url selector is needed.
- Store tokens in `ConfigEntry` data; implement refresh or reauth.
- Implement `DataUpdateCoordinator` for periodic state polling.
- Do not log credentials or raw tokens.

## Deliverables
Create a repo-ready folder tree:
- custom_components/yeedi/__init__.py
- custom_components/yeedi/manifest.json
- custom_components/yeedi/const.py
- custom_components/yeedi/config_flow.py
- custom_components/yeedi/coordinator.py
- custom_components/yeedi/api.py
- custom_components/yeedi/vacuum.py
- custom_components/yeedi/diagnostics.py
- custom_components/yeedi/translations/en.json
- README.md

## Behavior
1) Authentication
- Implement an `YeediApiClient` with:
  - `async_login(username, password, region=None) -> AuthResult`
  - `async_refresh_token(...)` if refresh exists
  - Central request method that sets required headers.
- Model auth state with `access_token`, `refresh_token` (optional), `expires_at` (optional).

2) Device discovery
- Implement `async_list_devices()` returning device descriptors.

3) Entity
- Implement `VacuumEntity` for each device with core methods:
  - async_start
  - async_pause
  - async_stop
  - async_return_to_base
  - optional: async_locate, fan_speed presets
- Expose: battery_level, status, supported_features.

4) Coordinator
- Poll interval: 30–60s (configurable later).
- Coordinator fetches status for each device efficiently.

5) Diagnostics
- Redact tokens, passwords, and any app identifiers.

## API uncertainties policy
Where the Node-RED flow is missing specifics, do NOT invent endpoints.
Instead:
- Create placeholder methods with clear TODOs.
- Centralize all endpoint paths in a single dict or dataclass.
- Raise `YeediApiError` with actionable messages.

## Acceptance checks
- The code imports cleanly under Home Assistant.
- Config flow can be launched and errors are handled.
- At least one vacuum entity can be created given valid API responses.

## Output
Provide the full file contents for all deliverables.
