#include <Arduino.h>

#include <ESP32Servo.h>
#include <LiquidCrystal.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// Las credenciales viven en secrets.h (fuera de git), igual que en el sketch de la camara.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copia secrets.example.h a secrets.h y llena las credenciales WiFi"
#endif

// Red WPA2-Enterprise (PEAP): se necesitan los cuatro valores.
#if !defined(WIFI_SSID) || !defined(EAP_IDENTITY) || !defined(EAP_USERNAME) || !defined(EAP_PASSWORD)
#error "secrets.h debe definir WIFI_SSID, EAP_IDENTITY, EAP_USERNAME y EAP_PASSWORD"
#endif

// ==================== HARDWARE ====================

#define PIR       35
#define SERVO_PIN 22
#define AUDIO     25
#define LED_A     14   // Red LED group
#define LED_B     13   // Other LED group

// LCD: RS, E, D4, D5, D6, D7
LiquidCrystal lcd(23, 21, 18, 17, 4, 15);

Servo servo;

// ==================== WIFI CONFIGURATION ====================

static const char *HOSTNAME = "sentry-alarm";
static const uint32_t WIFI_ATTEMPT_MS = 40000;       // un intento de login PEAP (Tec puede ser lento)
static const uint32_t WIFI_RETRY_DELAY_MS = 15000;   // pausa entre intentos
static const uint8_t WIFI_MAX_ATTEMPTS = 5;          // luego reinicia la placa
static const uint32_t WIFI_LOST_RETRY_MS = 60000;    // reconectar a mano si el driver no lo logro
static const uint32_t WIFI_LOST_RESTART_MS = 300000; // reiniciar si lleva 5 min sin WiFi

// ==================== API CONFIGURATION ====================

// PPE status of the people in front of the camera, read from the Pi's database:
//   {"estado": "OK" | "FALTA" | "SIN_SENAL", "texto": "Falta: lentes de seguridad y bata", ...}
// The ESP32 cannot use Tailscale, so this is the Pi's address on the Tec network
// (read-only copy of the dashboard on port 5002). The IP is assigned by DHCP:
// update it here if the Pi's address changes (on the Pi: hostname -I).
const char *API_URL = "http://10.43.55.2:5002/api/epp";

bool alarmaActiva = false;
bool alarmaAnterior = false;

unsigned long ultimaConsulta = 0;
const unsigned long INTERVALO_CONSULTA = 1000;

// Short timeouts: the request blocks loop(), so a Pi that is down must not
// freeze the PIR, the servo and the LCD for seconds at a time.
const uint16_t API_CONNECT_TIMEOUT_MS = 1000;
const uint16_t API_TIMEOUT_MS = 1500;

// ==================== PIR VARIABLES ====================

bool lado = false;
bool antes = false;
unsigned long t = 0;

// ==================== SERVO LENTO ====================

// Cada SERVO_PASO_MS el servo avanza 1 grado hacia el objetivo.
// 0 -> 180 tarda 180 * SERVO_PASO_MS (50 ms = 9 s). Sube el numero para ir aun mas lento.
const unsigned long SERVO_PASO_MS = 50;
int servoPos = 0;
int servoObjetivo = 0;
unsigned long ultimoPasoServo = 0;

void actualizarServo() {
    if (servoPos == servoObjetivo) return;
    if (millis() - ultimoPasoServo < SERVO_PASO_MS) return;
    ultimoPasoServo = millis();
    servoPos += (servoObjetivo > servoPos) ? 1 : -1;
    servo.write(servoPos);
}

// ==================== LCD ====================

String apiTexto = "Esperando API";   // lo ultimo que llego del API (o el error)
String apiTextoPrevio = "";
String lcdSuperiorPrevio = "";
unsigned long ultimoScroll = 0;
int scrollPos = 0;
const unsigned long INTERVALO_SCROLL = 350;

// The 16x2 LCD (HD44780) has no accented letters: "Sin señal de la cámara"
// would show garbage. Replace the Spanish UTF-8 letters with plain ASCII.
String paraLCD(String s) {
    const char *pares[][2] = {
        {"á", "a"}, {"é", "e"}, {"í", "i"}, {"ó", "o"}, {"ú", "u"}, {"ü", "u"}, {"ñ", "n"},
        {"Á", "A"}, {"É", "E"}, {"Í", "I"}, {"Ó", "O"}, {"Ú", "U"}, {"Ü", "U"}, {"Ñ", "N"},
    };
    for (auto &p : pares) s.replace(p[0], p[1]);
    return s;
}

// Escribe una linea completa (16 caracteres) sin usar lcd.clear(): no parpadea.
void lcdLinea(uint8_t fila, String s) {
    while (s.length() < 16) s += ' ';
    lcd.setCursor(0, fila);
    lcd.print(s.substring(0, 16));
}

// Fila 0: estado (alerta / movimiento / listo).
// Fila 1: lo que responde el API; si no cabe en 16 caracteres, se desplaza.
void actualizarLCD(bool pir) {
    String sup = alarmaActiva ? "ALERTA ACTIVA" : (pir ? "Movimiento!" : "Listo");
    if (sup != lcdSuperiorPrevio) {
        lcdLinea(0, sup);
        lcdSuperiorPrevio = sup;
    }

    if (apiTexto != apiTextoPrevio) {   // llego algo nuevo
        apiTextoPrevio = apiTexto;
        scrollPos = 0;
        ultimoScroll = 0;
        if (apiTexto.length() <= 16) lcdLinea(1, apiTexto);
    }

    if (apiTexto.length() > 16 && millis() - ultimoScroll >= INTERVALO_SCROLL) {
        ultimoScroll = millis();
        String cinta = apiTexto + "   ";
        int n = cinta.length();
        String ventana = "";
        for (int i = 0; i < 16; i++) ventana += cinta[(scrollPos + i) % n];
        lcdLinea(1, ventana);
        scrollPos = (scrollPos + 1) % n;
    }
}

// ==================== WIFI ENTERPRISE ====================

// Por que el AP nos rechazo o nos solto (codigos 802.11).
static const char *disconnectReason(uint8_t reason) {
    switch (reason) {
        case 2:   return "autenticacion expirada";
        case 15:  return "timeout del handshake: contrasena incorrecta?";
        case 23:  return "fallo 802.1X: usuario o contrasena incorrectos?";
        case 201: return "red no encontrada: SSID incorrecto o fuera de alcance";
        case 202: return "fallo de autenticacion: usuario o contrasena incorrectos?";
        case 203: return "fallo de asociacion";
        case 204: return "timeout del handshake: contrasena incorrecta o senal debil?";
        default:  return "";
    }
}

static void logDisconnect(WiFiEvent_t event, WiFiEventInfo_t info) {
    uint8_t reason = info.wifi_sta_disconnected.reason;
    Serial.printf("\nWiFi desconectado, motivo %u %s\n", reason, disconnectReason(reason));
}

// Un intento de login PEAP: estado WiFi limpio, pausa corta, begin() enterprise.
static bool tryWiFiOnce() {
    WiFi.disconnect(true, true);
    delay(200);
    WiFi.setHostname(HOSTNAME);  // antes de mode(), o algunos cores lo ignoran
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);

    Serial.printf("MAC %s, conectando a %s", WiFi.macAddress().c_str(), WIFI_SSID);
    // WPA2-Enterprise, PEAP: identidad anonima, luego usuario y contrasena de la cuenta.
    WiFi.begin(WIFI_SSID, WPA2_AUTH_PEAP, EAP_IDENTITY, EAP_USERNAME, EAP_PASSWORD);

    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_ATTEMPT_MS) {
        delay(500);
        Serial.print(".");
    }
    if (WiFi.status() != WL_CONNECTED) {
        Serial.printf("\nSin conexion tras %u s (WiFi.status = %d)\n",
                      (unsigned)(WIFI_ATTEMPT_MS / 1000), WiFi.status());
        return false;
    }
    return true;
}

// Espera hasta `ms`; regresa true antes si un login lento termina mientras tanto.
static bool waitForLateConnection(uint32_t ms) {
    uint32_t start = millis();
    while (millis() - start < ms) {
        if (WiFi.status() == WL_CONNECTED) return true;
        delay(250);
    }
    return WiFi.status() == WL_CONNECTED;
}

// Reintenta en sitio en lugar de reiniciar: un reinicio corta un login PEAP lento a la mitad
// y muchos logins fallidos pueden bloquear el dispositivo por un rato.
void connectWiFi() {
    for (uint8_t attempt = 1; attempt <= WIFI_MAX_ATTEMPTS; attempt++) {
        bool connected = tryWiFiOnce();
        if (!connected && attempt < WIFI_MAX_ATTEMPTS) {
            Serial.printf("Reintentando en %u s (intento %u de %u)\n",
                          (unsigned)(WIFI_RETRY_DELAY_MS / 1000), attempt + 1, WIFI_MAX_ATTEMPTS);
            lcd.clear();
            lcd.print("Reintentando");
            lcd.setCursor(0, 1);
            lcd.print("WiFi ");
            lcd.print(attempt + 1);
            lcd.print("/");
            lcd.print(WIFI_MAX_ATTEMPTS);
            connected = waitForLateConnection(WIFI_RETRY_DELAY_MS);
            if (connected) Serial.println("El login termino tarde: se conserva la conexion");
        }
        if (connected) {
            WiFi.setSleep(false);  // otra vez: algunos cores lo reinician al conectar
            Serial.printf("Conectado: IP %s, senal %d dBm\n",
                          WiFi.localIP().toString().c_str(), WiFi.RSSI());
            return;
        }
    }
    Serial.println("No se pudo conectar, reiniciando");
    lcd.clear();
    lcd.print("Sin WiFi");
    delay(1000);
    ESP.restart();
}

// Vigila el WiFi desde loop() sin bloquear el PIR/alarma:
// el driver reconecta solo; si pasa 1 min, se pide reconectar; a los 5 min, reinicia.
void vigilarWiFi() {
    static uint32_t wifiLostSince = 0;
    static bool reconexionPedida = false;

    if (WiFi.status() != WL_CONNECTED) {
        if (wifiLostSince == 0) {
            wifiLostSince = millis();
            reconexionPedida = false;
            Serial.println("WiFi perdido, reconectando...");
        } else if (!reconexionPedida && millis() - wifiLostSince > WIFI_LOST_RETRY_MS) {
            Serial.println("WiFi sigue caido, forzando reconexion");
            WiFi.reconnect();
            reconexionPedida = true;
        } else if (millis() - wifiLostSince > WIFI_LOST_RESTART_MS) {
            Serial.println("WiFi caido demasiado tiempo, reiniciando");
            ESP.restart();
        }
    } else if (wifiLostSince != 0) {
        wifiLostSince = 0;
        WiFi.setSleep(false);
        Serial.printf("WiFi de vuelta: IP %s\n", WiFi.localIP().toString().c_str());
    }
}

// ==================== API REQUEST ====================

// Reads /api/epp and decides the alarm:
//   "FALTA"     -> alarm on, the LCD shows what is missing ("Falta: bata", ...)
//   "OK"        -> alarm off, the LCD shows "OK"
//   "SIN_SENAL" -> alarm off (the camera is down, nobody can be judged), the LCD says so
// If the Pi cannot be reached, the alarm is switched off rather than left
// sounding on stale data, and the LCD shows the error.
void consultarAlarma() {

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi disconnected. Cannot query API.");
        apiTexto = "Sin WiFi";
        alarmaActiva = false;
        return;
    }

    HTTPClient http;

    http.setConnectTimeout(API_CONNECT_TIMEOUT_MS);
    http.setTimeout(API_TIMEOUT_MS);
    http.begin(API_URL);

    int httpCode = http.GET();

    if (httpCode == HTTP_CODE_OK) {

        String payload = http.getString();

        JsonDocument doc;
        DeserializationError error = deserializeJson(doc, payload);

        if (!error && doc["estado"].is<const char *>() && doc["texto"].is<const char *>()) {

            String estado = doc["estado"].as<String>();
            alarmaActiva = (estado == "FALTA");
            apiTexto = paraLCD(doc["texto"].as<String>());

            Serial.printf("API: %s | personas %d | lentes %s | bata %s -> alarma %s\n",
                          estado.c_str(), doc["personas"] | 0,
                          (doc["falta_lentes"] | false) ? "FALTA" : "ok",
                          (doc["falta_bata"] | false) ? "FALTA" : "ok",
                          alarmaActiva ? "ACTIVA" : "inactiva");

        } else {
            Serial.print("Invalid API response: ");
            Serial.println(payload);
            apiTexto = "Respuesta invalida del API";
            alarmaActiva = false;
        }

    } else {

        Serial.printf("HTTP request failed. Code: %d (%s)\n",
                      httpCode, HTTPClient::errorToString(httpCode).c_str());
        apiTexto = httpCode > 0 ? "API error HTTP " + String(httpCode) : "Sin conexion con la Raspberry";
        alarmaActiva = false;
    }

    http.end();
}

// ==================== SETUP ====================

void setup() {

    Serial.begin(115200);

    pinMode(PIR, INPUT);
    pinMode(LED_A, OUTPUT);
    pinMode(LED_B, OUTPUT);
    pinMode(AUDIO, OUTPUT);

    servo.attach(SERVO_PIN);
    servo.write(0);

    lcd.begin(16, 2);
    lcd.print("Conectando WiFi");

    WiFi.onEvent(logDisconnect, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);  // explica por que falla un login
    connectWiFi();

    lcd.clear();   // actualizarLCD() dibuja las dos filas desde aqui
}

// ==================== MAIN LOOP ====================

void loop() {

    // 0. Vigilar el WiFi (no bloquea)

    vigilarWiFi();

    // 1. Query API every second

    if (millis() - ultimaConsulta >= INTERVALO_CONSULTA) {

        ultimaConsulta = millis();

        consultarAlarma();
    }

    // 2. Read PIR sensor

    bool pir = digitalRead(PIR);

    // 3. API alarm has priority over PIR behavior

    if (alarmaActiva) {

        digitalWrite(LED_A, HIGH);
        digitalWrite(LED_B, LOW);

        tone(AUDIO, 1500);

        servoObjetivo = 0;   // regresa a 0 despacio

        if (!alarmaAnterior) Serial.println("ALARM ACTIVATED");

    } else {

        // Original PIR behavior

        if (pir) {

            if (millis() - t >= 200) {

                t = millis();

                lado = !lado;

                digitalWrite(LED_A, lado);
                digitalWrite(LED_B, lado);

                tone(AUDIO, lado ? 1500 : 1000);
            }

            // El servo barre de un extremo al otro, lento, sin depender del parpadeo
            if (servoPos == servoObjetivo) servoObjetivo = (servoObjetivo == 0) ? 180 : 0;

        } else {

            digitalWrite(LED_A, LOW);
            digitalWrite(LED_B, LOW);

            noTone(AUDIO);
            digitalWrite(AUDIO, LOW);

            servoObjetivo = 0;   // regresa a 0 despacio
        }

        if (pir != antes) Serial.println(pir ? "Movimiento" : "Sin movimiento");

        if (alarmaAnterior) Serial.println("ALARM DEACTIVATED");
    }

    // 4. Mover el servo un paso si toca

    actualizarServo();

    // 4b. LCD: estado arriba, respuesta del API abajo

    actualizarLCD(pir);

    // 5. Update previous states

    antes = pir;
    alarmaAnterior = alarmaActiva;
}
