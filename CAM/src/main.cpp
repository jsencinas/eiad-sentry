#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"
#include <WiFi.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include "esp_wpa2.h"
#include <esp_camera.h>

// WIFI DEFS
const char* ssid = "Tec";
#define EAP_ANON     "Tec"
#define EAP_USERNAME "A01648636"
#define EAP_PASSWORD "Pikabu231206!"

const int  MAX_INTENTOS_CONEXION   = 20;
const int  MAX_RECONEXIONES        = 3;
const unsigned long ESPERA_RECONEXION = 30000;

WiFiServer server(80);

bool initCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  bool hasPsram = psramFound();
  if (hasPsram) {
    config.frame_size   = FRAMESIZE_VGA; // 640x480
    config.jpeg_quality = 12;            // 0-63 (lower = higher quality)
    config.fb_count     = 2;             // Double buffer in PSRAM
    config.fb_location  = CAMERA_FB_IN_PSRAM;
    config.grab_mode    = CAMERA_GRAB_LATEST; // Always grab freshest frame for low latency
  } else {
    config.frame_size   = FRAMESIZE_CIF;
    config.jpeg_quality = 15;
    config.fb_count     = 1;
    config.fb_location  = CAMERA_FB_IN_DRAM;
    config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&config);

  // Fallback for sensors without hardware JPEG (e.g., RHYX M21-45 / GC2145)
  if (err == ESP_ERR_NOT_SUPPORTED) {
    Serial.println("Sensor lacks hardware JPEG. Re-initializing in PIXFORMAT_RGB565 with JIT JPEG compression...");
    config.pixel_format = PIXFORMAT_RGB565;
    config.frame_size   = hasPsram ? FRAMESIZE_QVGA : FRAMESIZE_QQVGA;
    config.fb_count     = hasPsram ? 2 : 1;
    config.fb_location  = hasPsram ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
    config.grab_mode    = hasPsram ? CAMERA_GRAB_LATEST : CAMERA_GRAB_WHEN_EMPTY;
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
      Serial.printf("Detected sensor: %s (PID: 0x%04x, HW JPEG: %s)\n",
                    info->name, s->id.PID, info->support_jpeg ? "YES" : "NO");
    }
  }

  return true;
}

void configurarEnterprise() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_STA);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_eap_client_set_identity((uint8_t *)EAP_ANON, strlen(EAP_ANON));
  esp_eap_client_set_username((uint8_t *)EAP_USERNAME, strlen(EAP_USERNAME));
  esp_eap_client_set_password((uint8_t *)EAP_PASSWORD, strlen(EAP_PASSWORD));
  esp_wifi_sta_enterprise_enable();
#else
  esp_wifi_sta_wpa2_ent_clear_ca_cert();
  esp_wifi_sta_wpa2_ent_set_identity((uint8_t *)EAP_ANON, strlen(EAP_ANON));
  esp_wifi_sta_wpa2_ent_set_username((uint8_t *)EAP_USERNAME, strlen(EAP_USERNAME));
  esp_wifi_sta_wpa2_ent_set_password((uint8_t *)EAP_PASSWORD, strlen(EAP_PASSWORD));
  esp_wifi_sta_wpa2_ent_enable();
#endif
}

bool conectarWiFi() {
  Serial.print("MAC Adress: ");
  Serial.println(WiFi.macAddress());
  Serial.print("Conectando a ");
  Serial.println(ssid);

  configurarEnterprise();
  WiFi.begin(ssid);

  int intentos = 0;
  while (WiFi.status() != WL_CONNECTED && intentos < MAX_INTENTOS_CONEXION) {
    delay(500);
    Serial.print(".");
    intentos++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nConectado a la red WiFi!");
    Serial.print("Direccion IP: ");
    Serial.println(WiFi.localIP());
    return true;
  }

  Serial.println("\nCould not connect to the Network. Check Username/Password.");
  return false;
}

void handleStreamClient(WiFiClient &client) {
  // 1. Read the first line of the HTTP request (e.g., "GET / HTTP/1.1")
  String reqLine = client.readStringUntil('\r');
  
  // Flush the remaining HTTP request headers until the blank line (\r\n\r\n)
  while (client.connected() && client.available()) {
    String headerLine = client.readStringUntil('\n');
    if (headerLine == "\r") break; 
  }

  // Disable Nagle's algorithm so frame packets go out immediately without TCP buffering delay
  client.setNoDelay(true);

  // 2. Send the main HTTP response header announcing a continuous MJPEG stream
  client.print(
    "HTTP/1.1 200 OK\r\n"
    "Access-Control-Allow-Origin: *\r\n"
    "Content-Type: multipart/x-mixed-replace; boundary=frameboundary\r\n"
    "Cache-Control: no-cache, no-store, must-revalidate\r\n"
    "Connection: close\r\n\r\n"
  );

  Serial.println("Client connected -> Starting MJPEG stream loop");

  // 3. Continuous Frame Push Loop
  while (client.connected()) {
    // Grab frame buffer from camera DMA
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Frame capture failed");
      break;
    }

    uint8_t *jpgBuf = nullptr;
    size_t jpgLen   = 0;
    bool converted  = false;

    if (fb->format == PIXFORMAT_JPEG) {
      jpgBuf = fb->buf;
      jpgLen = fb->len;
    } else {
      // JIT software JPEG compression (quality 1-100, higher = better)
      converted = frame2jpg(fb, 80, &jpgBuf, &jpgLen);
      // Release DMA frame buffer immediately so next frame can be captured during Wi-Fi TX
      esp_camera_fb_return(fb);
      fb = nullptr;

      if (!converted) {
        Serial.println("JIT JPEG compression failed");
        break;
      }
    }

    // Format and send the sub-header for this individual JPEG frame
    char frameHeader[128];
    int headerLen = snprintf(
      frameHeader, sizeof(frameHeader),
      "--frameboundary\r\n"
      "Content-Type: image/jpeg\r\n"
      "Content-Length: %u\r\n\r\n",
      (unsigned int)jpgLen
    );
    client.write((const uint8_t *)frameHeader, headerLen);

    // Write the raw JPEG bytes directly to the TCP socket
    size_t bytesSent = client.write(jpgBuf, jpgLen);

    // Terminate the frame chunk
    client.write((const uint8_t *)"\r\n", 2);

    // Return the frame buffer (if hardware JPEG) or free the JIT JPEG buffer
    if (fb) {
      esp_camera_fb_return(fb);
    }
    if (converted && jpgBuf) {
      free(jpgBuf);
    }

    // If the client closed the browser tab or connection dropped, exit loop
    if (bytesSent == 0) {
      break;
    }

    // Yield briefly to let the background Wi-Fi/TCP stack process packets
    delay(1);
  }

  client.stop();
  Serial.println("Client disconnected");
}

void setup(){
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // Prevent voltage dip resets on Wi-Fi TX spikes
  Serial.begin(115200);
  Serial.println("Starting the ESP32-CAM");

  if (!initCamera()) {
    Serial.println("Camera initialization failed! Check ribbon cable or PSRAM settings.");
    while (true) { delay(1000); }
  }

  conectarWiFi();
  server.begin();
}

void loop(){
  WiFiClient client = server.available();
  if (client) {
    handleStreamClient(client);
  }
}