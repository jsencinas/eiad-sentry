// Copy this file to config.h (git-ignored, it holds secrets) and fill it in.
#pragma once

// --- WiFi -------------------------------------------------------------------
#define WIFI_SSID     "network-name"
#define WIFI_PASSWORD "network-password"

// Enterprise networks (WPA2-Enterprise / PEAP, user + password login):
// uncomment these three lines. WIFI_PASSWORD is then the account password.
// Note: the server certificate of the network is not checked, so only use
// this on a network you trust.
// #define WIFI_ENTERPRISE
// #define WIFI_EAP_IDENTITY "user@domain"
// #define WIFI_EAP_USERNAME "user@domain"

// --- Upload: the Pi's Tailscale Funnel URL ----------------------------------
// Shown by `tailscale funnel status` on the Pi, without https:// .
#define UPLOAD_HOST  "pi-name.tailnet-name.ts.net"
#define UPLOAD_PORT  443        // Funnel allows 443, 8443 or 10000
#define UPLOAD_PATH  "/frame"
#define UPLOAD_TOKEN "same-token-as-frame_ingest.py"

// --- Camera -----------------------------------------------------------------
#define FRAME_SIZE   FRAMESIZE_VGA   // 640x480, what the detector expects
#define JPEG_QUALITY 12              // 10-63: lower = better image, bigger frames
#define MAX_FPS      10              // the Pi analyzes fewer than this anyway
