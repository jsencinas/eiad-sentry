// Copy this file to secrets.h (git-ignored) and fill it in.
// Never put real credentials in main.cpp: this repository is public.
#pragma once

#define WIFI_SSID     "Tec"

// WPA2-Enterprise (Tec): identity, user name and password of the account.
// For a normal WPA2 network, comment out the three EAP_ lines and set:
// #define WIFI_PASSWORD "network-password"
#define EAP_IDENTITY  "Tec"
#define EAP_USERNAME  "A0XXXXXXX"
#define EAP_PASSWORD  "account-password"
