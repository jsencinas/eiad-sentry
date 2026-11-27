// Copy this file to secrets.h (git-ignored) and fill it in.
// Never put real credentials in main.cpp: this repository is public.
#pragma once

#define WIFI_SSID     "Tec"

// WPA2-Enterprise (PEAP): anonymous identity, then the account's user name and
// password. All four values are required.
#define EAP_IDENTITY  "Tec"
#define EAP_USERNAME  "A0XXXXXXX"
#define EAP_PASSWORD  "account-password"
