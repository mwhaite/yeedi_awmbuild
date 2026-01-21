# Yeedi Home Assistant Integration (MVP)

This repository contains a Home Assistant custom integration for Yeedi robot vacuums.
It is currently MVP-focused and expects Yeedi cloud API details from the Home Assistant
forum Node-RED flow to be applied in `custom_components/yeedi/api.py`.

## Quick Start
1. Copy `custom_components/yeedi` into your Home Assistant config directory.
2. Restart Home Assistant.
3. Add the integration via UI: Settings -> Devices & Services -> Add Integration -> Yeedi.
4. Provide credentials and any region/base URL required by your Yeedi account.

## Development Notes
- API endpoints and request/response parsing live in `custom_components/yeedi/api.py`.
  Update `ENDPOINTS` and parsing logic using the Node-RED flow details.
- Tokens are stored in the config entry and refreshed when possible.
- Polling uses `DataUpdateCoordinator` with a default interval of 60 seconds.

## Testing
- No automated tests are included yet. When added, use `pytest` from the repo root.

## Troubleshooting
- If setup fails, check logs for `YeediApiError` messages. Ensure endpoints and
  auth fields match the Node-RED flow.
