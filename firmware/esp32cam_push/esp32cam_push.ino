// ESP32-CAM (AI Thinker): push JPEG frames over HTTPS to the Pi's ingest server.
//
// The camera and the Pi are on different networks and the ESP32 cannot run
// Tailscale, so instead of serving a stream it POSTs each frame to the Pi's
// public Tailscale Funnel URL (frame_ingest.py on the Pi). One TLS connection
// is reused for every frame; the handshake only happens on (re)connect.
//
// Arduino IDE: board "AI Thinker ESP32-CAM", ESP32 core 3.x, PSRAM enabled,
// partition scheme "Huge APP". Copy config.example.h to config.h first.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include "esp_camera.h"

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill it in"
#endif
#include "certs.h"

// AI Thinker ESP32-CAM pins
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

static const uint32_t RESPONSE_TIMEOUT_MS = 10000;
static const uint32_t RETRY_DELAY_MS = 3000;
static const uint32_t BAD_TOKEN_DELAY_MS = 30000;
static const uint32_t STATS_INTERVAL_MS = 5000;

WiFiClientSecure client;
uint32_t framesSent = 0, bytesSent = 0, lastStats = 0;

void initCamera() {
  camera_config_t c = {};
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_0;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAME_SIZE;
  c.jpeg_quality = JPEG_QUALITY;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;  // always send the newest frame, never a queued one

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed (0x%x), restarting\n", err);
    delay(5000);
    ESP.restart();
  }
}

void connectWiFi() {
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // power saving cuts upload speed a lot
#ifdef WIFI_ENTERPRISE
  WiFi.begin(WIFI_SSID, WPA2_AUTH_PEAP, WIFI_EAP_IDENTITY, WIFI_EAP_USERNAME, WIFI_PASSWORD);
#else
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
#endif
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > 30000) {
      Serial.println(" timeout, restarting");
      ESP.restart();
    }
    delay(500);
    Serial.print(".");
  }
  Serial.printf(" connected, IP %s, signal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
}

bool connectServer() {
  Serial.printf("Connecting to https://%s:%d ...\n", UPLOAD_HOST, UPLOAD_PORT);
  if (!client.connect(UPLOAD_HOST, UPLOAD_PORT)) {
    char err[100] = "";
    client.lastError(err, sizeof(err));
    Serial.printf("TLS connection failed: %s\n", err);
    return false;
  }
  Serial.println("Connected, sending frames");
  return true;
}

bool sendFrame(const camera_fb_t *fb) {
  char head[512];
  int n = snprintf(head, sizeof(head),
                   "POST " UPLOAD_PATH " HTTP/1.1\r\n"
                   "Host: " UPLOAD_HOST "\r\n"
                   "Authorization: Bearer " UPLOAD_TOKEN "\r\n"
                   "Content-Type: image/jpeg\r\n"
                   "Content-Length: %u\r\n"
                   "Connection: keep-alive\r\n\r\n",
                   (unsigned)fb->len);
  if (n <= 0 || n >= (int)sizeof(head)) return false;
  if (client.write((const uint8_t *)head, n) != (size_t)n) return false;
  size_t off = 0;
  while (off < fb->len) {
    size_t w = client.write(fb->buf + off, min((size_t)4096, fb->len - off));
    if (w == 0) return false;
    off += w;
  }
  return true;
}

// Reads one line (without \r\n) before the deadline. False on timeout or disconnect.
bool readLine(String &line, uint32_t deadline) {
  line = "";
  while ((int32_t)(deadline - millis()) > 0) {
    int c = client.read();
    if (c < 0) {
      if (!client.connected()) return false;
      delay(1);
      continue;
    }
    if (c == '\n') {
      line.trim();
      return true;
    }
    if (line.length() < 256) line += (char)c;
  }
  return false;
}

// Returns the HTTP status (-1 on error) and drains the body so the connection can be reused.
int readResponse(bool &keepAlive) {
  uint32_t deadline = millis() + RESPONSE_TIMEOUT_MS;
  String line;
  keepAlive = true;
  if (!readLine(line, deadline) || !line.startsWith("HTTP/1.") || line.length() < 12) return -1;
  int status = line.substring(9, 12).toInt();
  int length = 0;
  bool headersDone = false;
  while (readLine(line, deadline)) {
    if (line.length() == 0) {
      headersDone = true;
      break;
    }
    line.toLowerCase();
    if (line.startsWith("content-length:")) length = line.substring(15).toInt();
    else if (line.startsWith("connection:") && line.indexOf("close") >= 0) keepAlive = false;
  }
  if (!headersDone) return -1;
  while (length > 0 && (int32_t)(deadline - millis()) > 0) {
    if (client.read() >= 0) length--;
    else if (!client.connected()) return -1;
    else delay(1);
  }
  return length == 0 ? status : -1;
}

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.printf("ESP32-CAM push, MAC %s\n", WiFi.macAddress().c_str());
  initCamera();
  connectWiFi();
  client.setCACert(ROOT_CA);
  lastStats = millis();
}

void loop() {
  static uint32_t lastFrame = 0;
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost");
    client.stop();
    WiFi.disconnect();
    connectWiFi();
    return;
  }
  if (!client.connected() && !connectServer()) {
    delay(RETRY_DELAY_MS);
    return;
  }

  uint32_t gap = 1000 / MAX_FPS;
  if (millis() - lastFrame < gap) delay(gap - (millis() - lastFrame));
  lastFrame = millis();

  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Camera capture failed");
    delay(100);
    return;
  }
  size_t len = fb->len;
  bool sent = sendFrame(fb);
  esp_camera_fb_return(fb);

  bool keepAlive = false;
  int status = sent ? readResponse(keepAlive) : -1;
  if (status == 204) {
    framesSent++;
    bytesSent += len;
  } else if (status == 401) {
    Serial.println("Token rejected (401): check UPLOAD_TOKEN in config.h");
    delay(BAD_TOKEN_DELAY_MS);
  } else if (status > 0) {
    Serial.printf("Server answered %d\n", status);  // e.g. 502 if frame_ingest.py is not running
    delay(RETRY_DELAY_MS);
  } else {
    Serial.println("Upload failed, reconnecting");
  }
  if (status <= 0 || !keepAlive) client.stop();

  uint32_t now = millis();
  if (now - lastStats >= STATS_INTERVAL_MS) {
    float secs = (now - lastStats) / 1000.0f;
    Serial.printf("%.1f fps sent, %u KB per frame, signal %d dBm\n", framesSent / secs,
                  framesSent ? (unsigned)(bytesSent / framesSent / 1000) : 0, WiFi.RSSI());
    framesSent = bytesSent = 0;
    lastStats = now;
  }
}
