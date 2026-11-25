# ESP32-CAM firmware: push frames to the Pi

The ESP32-CAM and the Raspberry Pi are on **different networks**, and the ESP32 cannot run Tailscale. So the camera does not serve a stream for the Pi to pull. It **pushes** each JPEG over HTTPS to a public [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) URL of the Pi:

```
ESP32-CAM --HTTPS POST /frame--> Tailscale Funnel --> Pi: frame_ingest.py --> ppe_detector.py
(any network with internet)      https://PI.TAILNET.ts.net   127.0.0.1:8083     127.0.0.1:8084/stream
```

The camera only needs outbound internet access. Every frame carries a secret token, and the public URL only accepts frames: it never serves video back (see "Security").

## 1. Pi: ingest server and Funnel

1. **Create the token** once and keep it in a file only you can read:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))" > ~/.sentry_token
   chmod 600 ~/.sentry_token
   ```
2. **Start the ingest server** (in the project's venv):
   ```bash
   python frame_ingest.py --token-file ~/.sentry_token
   ```
   ```
   [ingest] Uploads: POST http://127.0.0.1:8083/frame (expose with: tailscale funnel)
   [ingest] Detector source: http://127.0.0.1:8084/stream
   [ingest] Waiting for the camera...
   ```
3. **Expose only the upload port** with Funnel:
   ```bash
   sudo tailscale funnel --bg 8083
   tailscale funnel status
   ```
   `status` prints the public URL, e.g. `https://raspberrypi.tail1234.ts.net`. The first time, the command gives a link to enable HTTPS certificates and Funnel for your tailnet in the Tailscale admin console. `--bg` keeps it running after you close the SSH session and after reboots; `sudo tailscale funnel --https=443 off` turns it off.
4. **Run the detector** on the local stream:
   ```bash
   python ppe_detector.py --demo --source http://127.0.0.1:8084/stream --save-dir out --web
   ```

## 2. Test from the laptop first (no ESP32 needed)

`tools/push_frames.py` sends the webcam exactly like the firmware does (one JPEG per request, one reused connection). Copy the token to the laptop and run:

```bash
python tools/push_frames.py https://raspberrypi.tail1234.ts.net/frame --token-file ~/.sentry_token
```
```
9.8 fps sent, 31 KB per frame
```

Test it from a network other than the Pi's (e.g. your phone's hotspot) to check the same path the camera will use. If this works, any problem left on the ESP32 is WiFi or configuration.

## 3. Flash the ESP32-CAM

1. **Arduino IDE:** in *Boards Manager*, install **esp32 by Espressif Systems** (version 3.x).
2. **Board settings** (*Tools*):
   - **Board:** AI Thinker ESP32-CAM
   - **PSRAM:** Enabled
   - **Partition Scheme:** Huge APP (3MB No OTA). TLS and the camera do not fit in the default partition.
3. **Configure:** in `firmware/esp32cam_push/`, copy `config.example.h` to `config.h` and fill in:
   - the WiFi network
   - `UPLOAD_HOST`: the Funnel host name, without `https://`
   - `UPLOAD_TOKEN`: the content of `~/.sentry_token`

   `config.h` is git-ignored because it holds the WiFi password and the token. For an enterprise WiFi with a user name and password (WPA2-Enterprise), uncomment the `WIFI_ENTERPRISE` lines.
4. **Wire it for flashing:** the board has no USB of its own.
   - An **ESP32-CAM-MB** base board: plug it in and connect by USB.
   - An **FTDI adapter**: 5V→5V, GND→GND, TX→U0R, RX→U0T, and a bridge from **GPIO0 to GND**. After uploading, remove the bridge and press reset.
5. **Open** `esp32cam_push.ino`, upload, and open the *Serial Monitor* at 115200:
   ```
   ESP32-CAM push, MAC 08:3A:F2:xx:xx:xx
   Connecting to network-name.... connected, IP 192.168.1.50, signal -58 dBm
   Connecting to https://raspberrypi.tail1234.ts.net:443 ...
   Connected, sending frames
   9.9 fps sent, 28 KB per frame, signal -58 dBm
   ```
   On the Pi, `frame_ingest.py` should now print `fps received`.

## Settings (`config.h`)

| Setting | Default | Notes |
|---|---|---|
| `FRAME_SIZE` | `FRAMESIZE_VGA` | 640x480, what the detector expects |
| `JPEG_QUALITY` | `12` | 10-63; lower is better quality and bigger frames |
| `MAX_FPS` | `10` | The Pi analyzes fewer frames than this; more only uses bandwidth |
| `UPLOAD_PORT` | `443` | Funnel only allows 443, 8443 and 10000 |

## Common problems

| Serial Monitor shows | Likely cause | Fix |
|---|---|---|
| `Copy config.example.h to config.h` (compile error) | No `config.h` | Create it from `config.example.h` |
| Dots forever, then `timeout, restarting` | Wrong WiFi details, or an enterprise network | Check `WIFI_SSID`/`WIFI_PASSWORD`; enable `WIFI_ENTERPRISE` if it asks for a user name |
| `TLS connection failed` | Wrong `UPLOAD_HOST`, Funnel off, or the network blocks HTTPS | Check `tailscale funnel status` on the Pi; try `push_frames.py` from another network |
| `Token rejected (401)` | `UPLOAD_TOKEN` differs from the Pi's token | Copy `~/.sentry_token` again, with no spaces or line breaks |
| `Server answered 502` | Funnel is on but `frame_ingest.py` is not running | Start it on the Pi |
| `Brownout detector was triggered`, constant resets | Weak power supply | 5 V, at least 2 A, short cable |
| `Camera init failed` | Camera ribbon loose, or PSRAM disabled | Reseat the ribbon; set *PSRAM: Enabled* |

## Security

- **What is public:** only `POST /frame` on port 8083. Requests without the right token get `401`, and that port never returns images. The video for the detector is on `127.0.0.1:8084`, a separate port that Funnel does not expose.
- **The token** is the only lock on the public URL. Keep it out of git (it lives in `config.h` and `~/.sentry_token`), and generate a new one if it leaks.
- **Encryption:** the frames travel over HTTPS. The firmware checks the certificate against the Let's Encrypt roots in `certs.h`, so it cannot be redirected to an impostor server.
- The live viewer (`ppe_detector.py --web`) is still reachable only over Tailscale.
