#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include "esp_wpa2.h"
#include "Pinout.h"

// ============================================================================
// WI-FI CONFIGURATION (WPA2-Enterprise - same as CAM module)
// ============================================================================
const char* ssid = "Tec";
#define EAP_ANON     "Tec"
#define EAP_USERNAME "A01648636"
#define EAP_PASSWORD "Pikabu231206!"

const int MAX_INTENTOS_CONEXION       = 20;
const unsigned long ESPERA_RECONEXION = 10000;

// ============================================================================
// RASPBERRY PI SERVER API CONFIGURATION
// Update RPI_HOST and RPI_PORT once the Raspberry Pi server is running
// ============================================================================
const char* RPI_HOST       = "192.168.1.100";
const uint16_t RPI_PORT    = 8000;
const char* ALARM_ENDPOINT = "/api/v1/alarm";

const unsigned long POLL_INTERVAL_MS = 2000; // Poll endpoint every 2 seconds
const uint16_t HTTP_TIMEOUT_MS       = 3000; // HTTP GET timeout in ms

// ============================================================================
// SIREN / TONE CONFIGURATION (PAM8403 on SPEAKER_AMP / GPIO 12)
// ============================================================================
const unsigned int TONE_FREQ_LOW_HZ       = 880;  // A5
const unsigned int TONE_FREQ_HIGH_HZ      = 1320; // E6
const unsigned long TONE_STEP_INTERVAL_MS = 300;  // Siren alternation period

// ============================================================================
// RUNTIME STATE
// ============================================================================
bool isAlarmActive                   = false;
bool highTonePhase                   = false;
unsigned long lastPollTimeMs         = 0;
unsigned long lastReconnectAttemptMs = 0;
unsigned long lastToneToggleMs       = 0;

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
  Serial.print("MAC Address: ");
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

void setAlarmState(bool active, const char* type, const char* message) {
  if (active) {
    if (!isAlarmActive) {
      Serial.println("========================================");
      Serial.println("[ALARM] >>> ALARM ACTIVATED <<<");
      Serial.printf("[ALARM] Type   : %s\n", (type != nullptr) ? type : "null (unspecified)");
      Serial.printf("[ALARM] Message: %s\n", (message != nullptr) ? message : "null (unspecified)");
      Serial.println("========================================");

      isAlarmActive    = true;
      highTonePhase    = false;
      lastToneToggleMs = millis();
      tone(SPEAKER_AMP, TONE_FREQ_LOW_HZ);
    } else {
      Serial.printf("[ALARM] Active (type: %s, message: %s)\n",
                    (type != nullptr) ? type : "null",
                    (message != nullptr) ? message : "null");
    }
  } else {
    if (isAlarmActive) {
      Serial.println("[ALARM] Alarm cleared -> Silencing speaker.");
    }
    isAlarmActive = false;
    noTone(SPEAKER_AMP);
    digitalWrite(SPEAKER_AMP, LOW);
  }
}

void updateAlarmTone() {
  if (!isAlarmActive) {
    return;
  }

  unsigned long now = millis();
  if (now - lastToneToggleMs >= TONE_STEP_INTERVAL_MS) {
    lastToneToggleMs = now;
    highTonePhase    = !highTonePhase;
    tone(SPEAKER_AMP, highTonePhase ? TONE_FREQ_HIGH_HZ : TONE_FREQ_LOW_HZ);
  }
}

void pollAlarmEndpoint() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  String url = String("http://") + RPI_HOST + ":" + String(RPI_PORT) + ALARM_ENDPOINT;

  WiFiClient client;
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);

  if (!http.begin(client, url)) {
    Serial.printf("[HTTP] Failed to initialize connection to %s\n", url.c_str());
    return;
  }

  http.addHeader("Accept", "application/json");
  int httpCode = http.GET();

  if (httpCode <= 0) {
    Serial.printf("[HTTP] GET %s failed, error: %s (%d)\n",
                  url.c_str(),
                  http.errorToString(httpCode).c_str(),
                  httpCode);
    http.end();
    return;
  }

  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("[HTTP] Unexpected status code: %d\n", httpCode);
    http.end();
    return;
  }

  String payload = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    Serial.printf("[JSON] Deserialization failed: %s\n", err.c_str());
    return;
  }

  bool success = doc["success"] | false;
  if (!success) {
    Serial.println("[API] Response reported success = false");
    return;
  }

  JsonVariantConst alarmObj = doc["alarm"];
  if (alarmObj.isNull()) {
    Serial.println("[API] Missing 'alarm' object in response");
    return;
  }

  bool active         = alarmObj["active"] | false;
  const char* type    = alarmObj["type"].as<const char*>();    // nullptr when JSON value is null
  const char* message = alarmObj["message"].as<const char*>(); // nullptr when JSON value is null

  setAlarmState(active, type, message);
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // Prevent voltage dip resets on Wi-Fi TX spikes
  Serial.begin(115200);
  Serial.println("Starting EIAD_SENTRY - ALARM Module");

  pinMode(IR_MOVEMENT, INPUT);
  pinMode(SPEAKER_AMP, OUTPUT);
  digitalWrite(SPEAKER_AMP, LOW);

  conectarWiFi();
}

void loop() {
  unsigned long now = millis();

  // Keep siren tone alternating non-blockingly whenever the alarm is active
  updateAlarmTone();

  // Reconnect Wi-Fi if connection dropped
  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastReconnectAttemptMs >= ESPERA_RECONEXION) {
      lastReconnectAttemptMs = now;
      Serial.println("[WiFi] Connection lost. Attempting reconnection...");
      conectarWiFi();
    }
    return;
  }

  // Poll GET /api/v1/alarm at configured interval
  if (now - lastPollTimeMs >= POLL_INTERVAL_MS) {
    lastPollTimeMs = now;
    pollAlarmEndpoint();
  }
}
