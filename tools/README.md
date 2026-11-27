# Stream tools: ESP32-CAM simulator and receiver

Until the ESP32-CAM arrives, a laptop webcam acts as the camera. These two tools send and receive the video over the network.

```
[Laptop]                                        [Raspberry Pi]
webcam -> laptop_cam_server.py  ---- WiFi ---->  rpi_receiver.py   (connection check)
          publishes MJPEG over HTTP              ppe_detector.py   (the detector)
          http://LAPTOP_IP:8081/stream
```

The real ESP32-CAM also publishes MJPEG over HTTP, so the Raspberry Pi code does not change: only the URL does (see "Switching to the real ESP32-CAM").

| File | Runs on | What it does |
|---|---|---|
| `laptop_cam_server.py` | Laptop | Takes the webcam (or a video file) and publishes it as an MJPEG stream on port 8081 |
| `rpi_receiver.py` | Raspberry Pi | Connects to that stream, reports fps and resolution, and saves `last_frame.jpg` every ~30 frames |

## 1. Laptop: publish the camera

**Requirements:** Python 3 and a webcam (only OpenCV is needed, no Flask).

```
pip install opencv-python
python tools/laptop_cam_server.py
```

**Check that it works:** open this in the laptop browser

```
http://localhost:8081/stream
```

If you see your camera live, the server is fine (`http://localhost:8081/` redirects there too). Leave the terminal open; stop it with `Ctrl+C`.

**Options:**

| Option | Default | Notes |
|---|---|---|
| `--camera` | `0` | Webcam index. If it doesn't open or opens the wrong camera, try `1` or `2`. A video file path also works and is played in a loop (handy without a webcam). |
| `--width` / `--height` | `640` / `480` | Similar to the ESP32-CAM. |
| `--quality` | `70` | JPEG quality; lower it if the network is slow. |
| `--port` | `8081` | 8081 rather than 81 because on Linux and macOS ports below 1024 need admin rights. |
| `--host` | `0.0.0.0` | Accepts connections from other machines (the Pi). |

On startup the server prints the URLs to use from other devices (LAN and Tailscale IPs). Don't use `0.0.0.0` on the Pi: it only means "listen on every interface" and is not an address you can connect to.

**Find the laptop's IP** by hand if needed:

| System | Command |
|---|---|
| Windows | `ipconfig` (look for the WiFi adapter's "IPv4 Address") |
| macOS | `ipconfig getifaddr en0` |
| Linux | `hostname -I` |
| With Tailscale | `tailscale ip -4` |

## 2. Raspberry Pi: receive the stream

**Requirements:** the laptop and the Pi must see each other on the network (same WiFi, or both on Tailscale).

On recent Raspberry Pi OS, global `pip install` is blocked. Use a virtual environment:

```
sudo apt install python3-full python3-venv
python3 -m venv ~/venv-stream
source ~/venv-stream/bin/activate
pip install opencv-python-headless
```

`opencv-python-headless` is the build without a GUI, the right one for a server without a display. Don't use `--break-system-packages`.

**Run it** (with the venv active and the simulator running on the laptop):

```
python tools/rpi_receiver.py http://LAPTOP_IP:8081/stream
```

**Expected output** (one line every ~5 s):

```
640x480 at 15.7 fps
640x480 at 15.3 fps
```

About 15 fps at 640x480 is normal for a laptop webcam. Stop it with `Ctrl+C`.

In a new SSH session, activate the venv again with `source ~/venv-stream/bin/activate`, or call `~/venv-stream/bin/python tools/rpi_receiver.py ...` directly.

## 3. See what the Raspberry Pi is receiving

The Pi has no display, so the receiver saves the latest frame to `last_frame.jpg` in the folder where you ran it. Ways to see it:

- **VS Code with Remote SSH:** click `last_frame.jpg` in the explorer. VS Code shows images. Close and reopen it to refresh.
- **`scp` from the laptop** (run the command on the laptop, not inside the SSH session):
  ```
  scp USER@RASPBERRY_IP:~/last_frame.jpg .
  ```
  It doesn't work the other way around because the laptop usually doesn't accept incoming SSH connections.
- **The detector's web viewer:** `ppe_detector.py --web` shows the processed video live in a browser (see the main README).

## Switching to the real ESP32-CAM

When the ESP32-CAM arrives with the example firmware (CameraWebServer), `laptop_cam_server.py` is no longer needed. On the Pi only the URL changes:

```
python tools/rpi_receiver.py http://ESP32_IP:81/stream
```

## Common problems

| Symptom | Likely cause | Fix |
|---|---|---|
| The camera doesn't open or the image is black | Another app is using the webcam, or the index is different | Close Zoom, Teams or other apps; try `--camera 1` |
| The Pi can't connect | Laptop firewall, wrong IP or different networks | Allow port 8081 in the firewall (on Windows, accept the prompt for private networks); check the IP; try `ping` from the Pi |
| It prints "Frame lost, reconnecting..." | The simulator stopped or the network dropped | Check that `laptop_cam_server.py` is still running; the receiver retries on its own |
| `externally-managed-environment` when installing | Global `pip` is blocked on the Pi | Use the venv from section 2 |
| Delayed image | OpenCV queues frames when processing is slower than the video | `ppe_detector.py` reads the stream in a separate thread that keeps only the newest frame |
| Low fps | Webcam, busy WiFi or high JPEG quality | Lower `--quality` or the resolution on the simulator; move the laptop closer to the router |

## Security

The simulator listens on `0.0.0.0`, so anyone on the same network who knows the IP can watch the camera. Only use it on a trusted network (or pass `--host` with your Tailscale IP) and stop it when you are done testing.
