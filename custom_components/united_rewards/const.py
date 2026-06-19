"""Constants for the United Rewards integration."""

from datetime import timedelta

DOMAIN = "united_rewards"

CONF_AUTH_TOKEN = "auth_token"
CONF_CUSTOMER_UUID = "customer_uuid"
CONF_DEVICE_TOKEN = "device_token"
CONF_HOUSEHOLD_ID = "household_id"
CONF_OKTA_ID = "okta_id"

DEFAULT_SCAN_INTERVAL = timedelta(hours=6)

BASE_URL = "https://www.shopunitedsupermarkets.com"
BANNER = "shopunitedsupermarkets"

CSMS_SUBSCRIPTION_KEY = "9e38e3f1d32a4279a49a264e0831ea46"
POINTS_HISTORY_SUBSCRIPTION_KEY = "4613afcdd5894d2fb1a0f499115b5d80"

OKTA_ISSUER = "https://ciam.albertsons.com/oauth2/ausp6soxrIyPrm8rS2p6"
OKTA_CLIENT_ID = "0oap6ku01XJqIRdl42p6"
OKTA_REDIRECT_URI = f"{BASE_URL}/bin/safeway/unified/sso/authorize"
OKTA_SCOPE = "openid profile email offline_access used_credentials metadata"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

PLATFORMS = ["sensor"]
