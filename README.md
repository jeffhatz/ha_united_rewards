# United Rewards for Home Assistant

Custom Home Assistant integration for polling United Supermarkets rewards points from the
`shopunitedsupermarkets.com` account APIs.

## What it creates

- `sensor.united_rewards_points`
- `sensor.united_rewards_expiring_points`
- `sensor.united_rewards_next_expiration_date`
- `sensor.united_rewards_dollar_discount`

The points sensor includes attributes for the household id, program type, dollar discount,
auto rewards points, and the point expiration schedule.

## Install

Copy `custom_components/united_rewards` into your Home Assistant `custom_components`
directory, restart Home Assistant, then add **United Rewards** from Settings > Devices
& services.

The config flow asks for:

- Email
- Password
- United household id, only if automatic detection fails

The site usually requires an emailed verification code during setup. Enter that code when
Home Assistant prompts for it.

The integration reads the `SWY_SHOP_TOKEN` returned by the site's `userinfo` endpoint after
SSO and uses the token's household id claim for the scorecard request. Accounts with no
reward points report `0` points.

## Notes

This uses private web APIs discovered from the United/Albertsons account web app, so it may
need maintenance if the site changes. The integration is deliberately small: all network
contract details live in `custom_components/united_rewards/api.py`.
