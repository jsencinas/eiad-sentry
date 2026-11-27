// ESP32-CAM: MJPEG stream for the Raspberry Pi detector.
//
//   http://CAMERA_IP/stream   (also http://CAMERA_IP/ and http://sentry-cam.local/stream)
//
// Tuned for frame rate:
// - WiFi power saving is off: with it on, the radio sleeps between packets
//   and the stream slows down several times over.
// - The stream runs on the ESP-IDF HTTP server (as in Espressif's
//   CameraWebServer). A send that fails or stalls for 3 s ends that client,
//   so a client that left never blocks the camera, and a frame is either
//   sent whole or the connection is closed (no cut-off, green frames).
// - Two frame buffers in PSRAM, always the newest frame: the camera captures
//   the next frame while the current one is being sent.
// - WiFi reconnects by itself; the board restarts if WiFi or the camera
//   stays down.
//
// Orientation: the camera is mounted turned 90 degrees, and the Pi straightens
// it (ppe_detector.py --rotate 270, about 1 ms per frame). Do not rotate here:
// rotating on the ESP32 means raw RGB565 frames and software JPEG, which cut
// the stream to ~3 fps at 320x240 instead of hardware JPEG at 640x480.
//
// WiFi credentials live in src/secrets.h (git-ignored): copy
// src/secrets.example.h. Never put them in this file: the repo is public.

#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include <atomic>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copy src/secrets.example.h to src/secrets.h and fill in the WiFi credentials"
#endif

static const char *HOSTNAME = "sentry-cam";       // http://sentry-cam.local/stream
static const int JPEG_QUALITY = 12;               // 10-63, lower = better image, bigger frames
static const int SOFT_JPEG_QUALITY = 80;          // sensors without hardware JPEG (1-100)
static const uint32_t WIFI_ATTEMPT_MS = 25000;      // one PEAP login attempt (Tec can be slow)
static const uint32_t WIFI_RETRY_DELAY_MS = 15000;  // pause between attempts
static const uint8_t WIFI_MAX_ATTEMPTS = 5;         // then restart the board as a last resort
static const uint32_t WIFI_LOST_RETRY_MS = 60000;   // reconnect by hand if the driver has not
static const uint32_t STATS_INTERVAL_MS = 5000;
static const uint8_t MAX_CAPTURE_FAILURES = 10;

#define PART_BOUNDARY "frameboundary"
static const char *STREAM_CONTENT_TYPE = "multipart/x-mixed-replace; boundary=" PART_BOUNDARY;
static const char *STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char *STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static httpd_handle_t streamServer = nullptr;

// Written by the HTTP server task, read by loop() for the stats line.
static std::atomic<uint32_t> framesSent{0};
static std::atomic<uint32_t> bytesSent{0};
static std::atomic<uint8_t> captureFailures{0};
static std::atomic<bool> clientStreaming{false};

bool initCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  bool hasPsram = psramFound();
  if (hasPsram) {
    config.frame_size = FRAMESIZE_VGA;          // 640x480
    config.jpeg_quality = JPEG_QUALITY;
    config.fb_count = 2;                        // capture the next frame while sending this one
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;      // never send a stale, queued frame
  } else {
    Serial.println("No PSRAM: falling back to CIF (400x296), one frame buffer");
    config.frame_size = FRAMESIZE_CIF;
    config.jpeg_quality = 15;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&config);

  // Sensors without hardware JPEG (e.g. RHYX M21-45 / GC2145): RGB565 + software JPEG.
  if (err == ESP_ERR_NOT_SUPPORTED) {
    Serial.println("Sensor lacks hardware JPEG: using RGB565 with software JPEG");
    config.pixel_format = PIXFORMAT_RGB565;
    config.frame_size = hasPsram ? FRAMESIZE_QVGA : FRAMESIZE_QQVGA;
    err = esp_camera_init(&config);
  }

  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    return false;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    camera_sensor_info_t *info = esp_camera_sensor_get_info(&s->id);
    if (info) {
      Serial.printf("Sensor: %s (PID 0x%04x, hardware JPEG: %s), PSRAM: %s\n", info->name, s->id.PID,
                    info->support_jpeg ? "yes" : "no", hasPsram ? "yes" : "no");
    }
  }
  return true;
}

// Why the access point dropped or refused us (802.11 reason codes).
static const char *disconnectReason(uint8_t reason) {
  switch (reason) {
    case 2: return "authentication expired";
    case 15: return "4-way handshake timeout: wrong password?";
    case 23: return "802.1X authentication failed: wrong user name or password?";
    case 201: return "network not found: wrong SSID or out of range";
    case 202: return "authentication failed: wrong user name or password?";
    case 203: return "association failed";
    case 204: return "handshake timeout: wrong password or weak signal?";
    default: return "";
  }
}

static void logDisconnect(WiFiEvent_t event, WiFiEventInfo_t info) {
  uint8_t reason = info.wifi_sta_disconnected.reason;
  Serial.printf("\nWiFi disconnected, reason %u %s\n", reason, disconnectReason(reason));
}

// One PEAP login attempt, the same way as the sketch that connects reliably:
// clean WiFi state, short pause, enterprise begin(). True if connected.
static bool tryWiFiOnce() {
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.setHostname(HOSTNAME);  // before mode(), or some cores ignore it
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // power saving cuts the stream rate several times over

  Serial.printf("MAC %s, connecting to %s", WiFi.macAddress().c_str(), WIFI_SSID);
#ifdef EAP_USERNAME
  WiFi.begin(WIFI_SSID, WPA2_AUTH_PEAP, EAP_IDENTITY, EAP_USERNAME, EAP_PASSWORD);
#else
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
#endif

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_ATTEMPT_MS) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("\nNo connection after %u s (WiFi.status = %d)\n", (unsigned)(WIFI_ATTEMPT_MS / 1000),
                  WiFi.status());
    return false;
  }
  WiFi.setSleep(false);  // again: some cores reset it when the connection comes up
  Serial.printf("\nConnected: IP %s, signal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  return true;
}

// Retries in place instead of restarting: a restart throws away a slow PEAP
// login half-way, and many failed logins can get the device blocked for a while.
void connectWiFi() {
  for (uint8_t attempt = 1; attempt <= WIFI_MAX_ATTEMPTS; attempt++) {
    if (tryWiFiOnce()) return;
    if (attempt < WIFI_MAX_ATTEMPTS) {
      Serial.printf("Retrying in %u s (attempt %u of %u)\n", (unsigned)(WIFI_RETRY_DELAY_MS / 1000), attempt + 1,
                    WIFI_MAX_ATTEMPTS);
      delay(WIFI_RETRY_DELAY_MS);
    }
  }
  Serial.println("Could not connect, restarting");
  ESP.restart();
}

static esp_err_t streamHandler(httpd_req_t *req) {
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-cache, no-store, must-revalidate");

  Serial.println("Stream client connected");
  clientStreaming = true;
  char part[96];

  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera capture failed");
      captureFailures++;
      res = ESP_FAIL;
      break;
    }
    captureFailures = 0;

    uint8_t *jpg = fb->buf;
    size_t len = fb->len;
    bool converted = false;
    if (fb->format != PIXFORMAT_JPEG) {
      converted = frame2jpg(fb, SOFT_JPEG_QUALITY, &jpg, &len);
      esp_camera_fb_return(fb);  // free the buffer now so the next capture can start
      fb = nullptr;
      if (!converted) {
        Serial.println("Software JPEG failed");
        res = ESP_FAIL;
        break;
      }
    }

    // Each send either goes out whole or fails (closed or stalled client):
    // then the connection is dropped instead of sending a cut-off frame.
    res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (res == ESP_OK) {
      int n = snprintf(part, sizeof(part), STREAM_PART, (unsigned)len);
      res = httpd_resp_send_chunk(req, part, n);
    }
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, (const char *)jpg, len);
    }

    if (fb) {
      esp_camera_fb_return(fb);
    } else if (converted) {
      free(jpg);
    }
    if (res != ESP_OK) break;

    framesSent++;
    bytesSent += len;
  }

  clientStreaming = false;
  Serial.println("Stream client disconnected");
  return res;
}

void startStreamServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.send_wait_timeout = 3;  // seconds: a stalled client is dropped, not waited on forever
  config.recv_wait_timeout = 3;
  config.lru_purge_enable = true;  // a new client can take the socket of a forgotten one

  if (httpd_start(&streamServer, &config) != ESP_OK) {
    Serial.println("HTTP server failed to start, restarting");
    delay(1000);
    ESP.restart();
  }
  httpd_uri_t stream = {};
  stream.method = HTTP_GET;
  stream.handler = streamHandler;
  stream.uri = "/stream";
  httpd_register_uri_handler(streamServer, &stream);
  stream.uri = "/";  // the old firmware streamed on / too
  httpd_register_uri_handler(streamServer, &stream);

  if (MDNS.begin(HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
  }
  Serial.printf("Stream: http://%s/stream  (or http://%s.local/stream)\n", WiFi.localIP().toString().c_str(),
                HOSTNAME);
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);  // avoid resets on WiFi TX current spikes
  Serial.begin(115200);
  Serial.println("\nStarting the ESP32-CAM");

  if (!initCamera()) {
    Serial.println("Check the camera ribbon and that PSRAM is enabled. Restarting in 5 s");
    delay(5000);
    ESP.restart();
  }
  WiFi.onEvent(logDisconnect, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);  // say why a login fails
  connectWiFi();
  startStreamServer();
}

void loop() {
  static uint32_t lastStats = millis();
  static uint32_t wifiLostSince = 0;

  // WiFi: the driver reconnects by itself; if it has not after a minute,
  // log in again by hand (which only restarts after several failed attempts).
  if (WiFi.status() != WL_CONNECTED) {
    if (wifiLostSince == 0) {
      wifiLostSince = millis();
      Serial.println("WiFi lost, reconnecting...");
    } else if (millis() - wifiLostSince > WIFI_LOST_RETRY_MS) {
      Serial.println("WiFi still down, logging in again");
      connectWiFi();
      wifiLostSince = 0;
    }
  } else if (wifiLostSince != 0) {
    wifiLostSince = 0;
    WiFi.setSleep(false);
    Serial.printf("WiFi back: IP %s\n", WiFi.localIP().toString().c_str());
  }

  // The camera does not recover from repeated capture failures by itself.
  if (captureFailures >= MAX_CAPTURE_FAILURES) {
    Serial.println("Camera keeps failing, restarting");
    ESP.restart();
  }

  uint32_t now = millis();
  if (now - lastStats >= STATS_INTERVAL_MS) {
    uint32_t frames = framesSent.exchange(0), bytes = bytesSent.exchange(0);
    float secs = (now - lastStats) / 1000.0f;
    if (clientStreaming || frames) {
      Serial.printf("%.1f fps sent, %u KB per frame, %u KB/s, signal %d dBm\n", frames / secs,
                    frames ? (unsigned)(bytes / frames / 1024) : 0, (unsigned)(bytes / secs / 1024), WiFi.RSSI());
    }
    lastStats = now;
  }
  delay(100);
}
