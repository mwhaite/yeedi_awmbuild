# Yeedi Home Assistant Integration

A custom Home Assistant integration for **Yeedi** brand robot vacuum cleaners.

## Overview

This integration allows you to control and monitor your Yeedi robot vacuum directly from Home Assistant. It uses the same underlying MQTT-based protocol as Ecovacs/Deebot but implements Yeedi-specific authentication endpoints.

## Supported Devices

This integration supports Yeedi devices that use the `eco-ng` (MQTT) protocol, including:

- Yeedi Vac 2 Pro
- Yeedi Vac Max
- Yeedi Vac Hybrid
- Yeedi Vac Station
- Yeedi Mop Station
- Yeedi K650
- Yeedi 2 Hybrid
- And other newer Yeedi models using the Yeedi app

## Features

- **Start/Stop/Pause cleaning** - Control cleaning sessions
- **Return to dock** - Send the vacuum back to its charging station
- **Locate** - Play a sound to find your vacuum
- **Battery monitoring** - Track battery level
- **Fan speed control** - Adjust suction power (if supported by device)
- **Room cleaning** - Clean specific rooms/areas (if supported by device)
- **Real-time state updates** - MQTT push notifications for instant status changes
- **Error reporting** - Detect and report error states

## Installation

### Manual Installation

1. Copy the `yeedi` folder into your Home Assistant `custom_components` directory:
   ```bash
   cp -r yeedi /config/custom_components/
   ```

2. Restart Home Assistant

3. Go to **Settings > Devices & Services > Add Integration**

4. Search for **"Yeedi"** and follow the setup flow

5. Enter your Yeedi account credentials:
   - **Email**: Your Yeedi app login email
   - **Password**: Your Yeedi app password
   - **Country**: The country where your Yeedi account is registered

### HACS (Home Assistant Community Store)

1. Add this repository as a custom repository in HACS
2. Install the Yeedi integration
3. Restart Home Assistant
4. Add the integration via Settings > Devices & Services

## Configuration

The integration is configured entirely through the UI (config flow). No YAML configuration is required.

### Options

| Option | Description | Default |
|--------|-------------|---------|
| Email | Your Yeedi account email | Required |
| Password | Your Yeedi account password | Required |
| Country | Country code (e.g., US, DE, UK) | Required |

### Self-Hosted Mode (Advanced)

For advanced users running a custom Bumper server or local MQTT broker:

- **Override REST URL**: Custom REST API endpoint
- **Override MQTT URL**: Custom MQTT broker URL
- **Verify MQTT Certificate**: Whether to verify MQTT TLS certificates

## Architecture

This integration uses:

- **Yeedi Authenticator** (`yeedi_auth.py`) - Custom authentication that handles Yeedi-specific API endpoints (`yeedi.com` vs `ecovacs.com`), app keys, and org values
- **deebot-client** - The excellent Python client library by DeebotUniverse for device communication via MQTT
- **Home Assistant Config Flow** - UI-based configuration
- **StateVacuumEntity** - Standard HA vacuum entity with `VacuumActivity` states

## Authentication Details

Yeedi uses a three-step authentication flow:

1. **Login API** - Authenticate with email + MD5(password) → get `uid` and `accessToken`
2. **Auth Code API** - Exchange `uid` + `accessToken` → get short-lived `authCode`
3. **Portal API** - Exchange `authCode` via `loginByItToken` → get long-lived `user_access_token`

The authenticator automatically refreshes tokens before they expire.

## Troubleshooting

### "Invalid authentication" error
- Double-check your email and password
- Ensure you're using your **Yeedi** account credentials (not Ecovacs)
- Verify the country matches where you registered your Yeedi account

### "Cannot connect" error
- Check your internet connection
- The Yeedi API servers may be temporarily unavailable - wait a few minutes and retry
- For self-hosted setups, verify your custom URLs are accessible

### Device not found
- Ensure your vacuum is online and connected to the Yeedi app
- Try restarting your vacuum and the integration
- Check that your vacuum uses the newer MQTT protocol (not legacy XMPP)

### Migration from Ecovacs integration
If you previously used the Ecovacs integration with a migrated Yeedi account:
1. Remove the Ecovacs integration configuration for your Yeedi device
2. Install and configure this Yeedi integration with your Yeedi account credentials

## Credits

- Built on top of the [deebot-client](https://github.com/DeebotUniverse/client.py) library by DeebotUniverse
- Authentication constants reverse-engineered from the [ecovacs-deebot.js](https://github.com/mrbungle64/ecovacs-deebot.js) library by mrbungle64
- Inspired by the Home Assistant core [Ecovacs](https://www.home-assistant.io/integrations/ecovacs) integration

## License

MIT License - See LICENSE for details
