"""
Access control config for the DAQ GUI (view-only mode).

Copy this file to access_config.py and edit it — access_config.py is gitignored
so your real password never lands in the repo:

    cp access_config.example.py access_config.py

Then restart the Flask server. If access_config.py is missing, the app fails
SAFE: only localhost can control, everyone else is view-only.

Who is allowed to CONTROL (start/stop runs, watchers, gas, HV, etc.):
  - any client whose IP is in WHITELIST_IPS or a WHITELIST_CIDRS subnet
    (silent — no login needed), OR
  - anyone who unlocks their browser session with CONTROL_PASSWORD.
Everyone else gets the full live view but their control buttons are disabled.
"""

# Individual IPs that get silent full control (no password prompt).
# Add your workstation / laptop here. Localhost is always safe to keep.
WHITELIST_IPS = [
    "127.0.0.1",
    "::1",
    # "192.168.1.50",     # <- your machine's IP on the DAQ network
]

# Optional whole-subnet whitelists, CIDR notation. Handy if your IP is handed
# out by DHCP and drifts within a range. Leave empty to disable.
WHITELIST_CIDRS = [
    # "192.168.1.0/24",   # <- everyone on the lab LAN can control
]

# Fallback password to unlock control from any IP (laptop on VPN, phone, a
# machine whose IP you didn't whitelist). Set to "" to disable password unlock
# entirely (whitelist-only). Pick something non-trivial.
CONTROL_PASSWORD = "change-me"

# Secret used to sign the session cookie that remembers a password unlock.
# Set this to a long random string (e.g. `python -c "import secrets;
# print(secrets.token_hex(32))"`). If it changes, everyone must re-unlock.
SECRET_KEY = "replace-with-a-long-random-string"
