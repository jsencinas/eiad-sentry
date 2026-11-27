# eiad-sentry

Personal protective equipment (PPE) detection for the EIAD. An ESP32-CAM streams video over WiFi to a Raspberry Pi 5, which runs YOLOv8 and raises an alert when a student is missing their lab coat, safety boots or safety glasses. There are no physical locks: it only alerts and logs.

```
ESP32-CAM (or laptop webcam) --MJPEG/WiFi--> Raspberry Pi 5: ppe_detector.py --> console, alert photos, live web viewer
```

## Project layout

| Path | What it is |
|---|---|
| `ppe_detector.py` | Entry point of the detector |
| `sentry/association.py` | Assigns PPE detections to people (pure Python, tested) |
| `sentry/smoothing.py` | Majority vote over time and per-person verdict (pure Python, tested) |
| `sentry/classes.py` | Checks that the model has the needed classes (pure Python, tested) |
| `sentry/stream.py` | Reads the stream in a thread, keeping only the newest frame, and reconnects |
| `sentry/inference.py` | YOLOv8 inference and tracking |
| `sentry/output.py` | Annotated frames, `on_violation` hook, console stats |
| `sentry/web.py` | Live web viewer (MJPEG in the browser) |
| `sentry/database.py` | Writes alerts and the live lab state to the lab-monitor PostgreSQL database |
| `CAM/` | ESP32-CAM firmware (PlatformIO) that serves the MJPEG stream on the same network: `http://CAMERA_IP/stream`. WiFi credentials go in `CAM/src/secrets.h` (git-ignored, copy `secrets.example.h`). The camera is mounted sideways, so run the detector with `--rotate 270` |
| `tools/` | ESP32-CAM simulator for the laptop and a stream check for the Pi ([guide](tools/README.md)) |
| `tests/` | `pytest` tests; they need no camera, model or OpenCV |
| `raspberry_setup.md` | How the Pi was connected to the Tec enterprise WiFi and Tailscale |

## Installation (Raspberry Pi)

Recent Raspberry Pi OS blocks global `pip` (PEP 668): use a venv, never `--break-system-packages`.

```bash
sudo apt install python3-full python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Install the CPU build of PyTorch first. On ARM, the default PyPI build of `torch` pulls in several GB of NVIDIA CUDA packages the Pi cannot use, and unpacking them can fill `/tmp` (a 4 GB RAM disk on the Pi). If pip still runs out of space, add `TMPDIR=~/.cache/pip-tmp` in front of the command.

If `ultralytics` pulled in `opencv-python` (GUI build) and you get `libGL` errors:

```bash
pip uninstall -y opencv-python
pip install --force-reinstall opencv-python-headless
```

The first run downloads `yolov8n.pt` automatically. Model weights are never committed (`*.pt`, `*.onnx` are in `.gitignore`).

## Demo mode (phase 1)

There is no trained PPE model yet, so demo mode checks the whole pipeline (stream → inference → tracking → smoothing → alert → annotated photo) with the COCO `yolov8n.pt` model and an everyday object standing in for PPE. A person is `OK` if the object's center falls inside their box, otherwise `MISSING`.

1. On the laptop, publish the webcam ([details](tools/README.md)):
   ```bash
   python tools/laptop_cam_server.py
   ```
2. On the Pi:
   ```bash
   python ppe_detector.py --demo --source http://LAPTOP_IP:8081/stream --save-dir out --web
   ```
3. Stand in front of the camera holding a backpack next to you → `OK`. Put it away → `MISSING` after a few frames, and an annotated photo appears in `out/`.

Other substitute objects: `--require bottle`, `--require cup`, `--require "cell phone"` (or several, comma separated).

### Console output

```
[web] Live viewer at http://100.74.216.50:8082/
[sentry] Mode: demo | required: backpack
[stream] Connected to http://100.112.61.47:8081/stream
[stats] 6.2 fps | inference 151 ms avg, 170 ms max | people: #1 OK
[ALERT] 2026-11-20 17:41:33 person #1 missing: backpack
[ALERT] saved out/20261120_174133_id1_backpack.jpg
```

A stats line (analysis fps and inference time per frame) is printed every 5 s. An alert is raised at most once every `--cooldown` seconds per person.

## ESP32-CAM

The camera and the Pi are on the same network (the Tec WiFi), and the detector reads the camera's stream directly. The firmware is in `CAM/` (flash it with PlatformIO, or open it in the Arduino IDE with **PSRAM: Enabled**). The camera is mounted sideways, so the Pi straightens the frames:

```bash
python ppe_detector.py --demo --source http://CAMERA_IP/stream --rotate 270 --save-dir out --web
```

The camera's IP is printed on its serial monitor (`Stream: http://.../stream`). The Tec network assigns it dynamically; a DHCP reservation for the camera's MAC keeps it fixed.

## Live web viewer

With `--web`, the processed video (boxes and verdicts) can be watched in a browser from another computer:

| URL | Content |
|---|---|
| `http://PI_IP:8082/` | Page with the live video and the current verdicts |
| `http://PI_IP:8082/stream` | MJPEG stream only |
| `http://PI_IP:8082/snapshot.jpg` | Latest annotated frame |
| `http://PI_IP:8082/status.json` | Verdicts and stats as JSON |

The video shows students, so by default the viewer **only listens on the Pi's Tailscale IP** (`100.x.y.z`): it is reachable from your own Tailscale devices and not from the rest of the Tec network. If Tailscale is not running, it falls back to `127.0.0.1`. Use `--web-host` to choose another address (`0.0.0.0` exposes it to the whole network: avoid it).

Frames are only JPEG-encoded while someone is watching, so the viewer costs almost nothing when nobody has it open.

## Coral Edge TPU

A Google Coral USB Accelerator runs the model about 6x faster than the Pi's CPU and leaves the CPU almost idle. Measured on the Pi 5 with the full pipeline (tracking, smoothing, database):

| Model | Inference | Analysis rate |
|---|---|---|
| `yolov8n.pt` on the CPU, `--imgsz 640` | 310 ms | 3.2 fps |
| `yolov8n.pt` on the CPU, `--imgsz 320` | 94 ms | ~10 fps |
| `yolov8n` on the Coral, `--imgsz 320` | 53 ms | 18.8 fps |

1. **Runtime on the Pi.** Google's own packages no longer work, so use the maintained builds. Download the `libedgetpu1-std_*.trixie_arm64.deb` (Raspberry Pi OS 13) or `*.bookworm_arm64.deb` (OS 12) from [feranick/libedgetpu](https://github.com/feranick/libedgetpu/releases), then:
   ```bash
   sudo apt install ./libedgetpu1-std_*_arm64.deb
   sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=1a6e
   pip install ai-edge-litert   # in the project venv
   ```
   Plug the Coral into a blue USB 3 port. `lsusb` shows `1a6e:089a Global Unichip` until the first model loads, then `18d1:9302 Google`. Your user must be in the `plugdev` group.
2. **Convert the model on an x86 Linux computer.** The compiler does not run on ARM. With Docker:
   ```bash
   docker run --rm -v "$PWD":/work -w /work python:3.11-slim bash -c \
     "apt-get update -qq && apt-get install -y -qq libgl1 libglib2.0-0 && pip install ultralytics && yolo export model=yolov8n.pt format=edgetpu imgsz=320"
   ```
   Copy `yolov8n_saved_model/yolov8n_full_integer_quant_edgetpu.tflite` to the Pi. Keep the `_edgetpu.tflite` ending: that is how the detector knows to use the Coral.
3. **Run it** with the same `--imgsz` the model was exported with:
   ```bash
   python ppe_detector.py --demo --model yolov8n_full_integer_quant_edgetpu.tflite --imgsz 320 --source ...
   ```

`tflite_runtime`, which Ultralytics expects, has no build for Python 3.13. The detector registers Google's `ai-edge-litert` under that name, so nothing else is needed. 320 px fits the whole model on the Coral and matches a 320x240 ESP32-CAM. The model is quantized to 8 bits, which costs a little accuracy.

## Lab-monitor database

With `--db`, the detector writes to the lab-monitor dashboard's PostgreSQL database. It needs migration `004_faltantes_y_permisos.sql` from the `lab-monitor` branch, and `pip install "psycopg[binary]"`.

```bash
python ppe_detector.py --demo --require "cell phone" --source http://ESP32_IP/stream --save-dir out \
    --db postgresql:///lab_monitor --db-map "cell phone=glasses"
```

| Table | What the detector writes |
|---|---|
| `incidencias` | One row per alert: `falta_lentes`, `falta_bata`, the readable `faltante` text, the photo path and the person's track number |
| `estado_laboratorio` | Live state every 2 s and on every change: people in view (`cantidad_personas`), how many lack glasses (`sin_lentes`) or a coat (`sin_bata`), and `deteccion_estado`. If no frames arrive for 5 s, or the detector stops, it becomes `SIN_SENAL` |

The database only knows two items, safety glasses and lab coat. The real model's `glasses` and `coat` map to them by themselves. In demo mode, `--db-map` says which one each substitute object stands for.

Database writes happen in a background thread: if the database is slow or down, detection carries on, alerts wait in a queue, and writing resumes when it is back (`[db] Writing again`).

`postgresql:///lab_monitor` connects through the local socket as your own user, who must be a member of the `lab_monitor` role.

## Arguments

```
python ppe_detector.py --source SOURCE [--demo] [--require OBJECTS] [--model WEIGHTS]
    [--conf 0.4] [--imgsz 640] [--save-dir out] [--cooldown 10] [--show]
    [--web] [--web-host HOST] [--web-port 8082] [--db DSN] [--db-map ITEM=glasses|coat]
    [--window 15] [--min-samples 5] [--stats-interval 5]
```

| Argument | Default | Meaning |
|---|---|---|
| `--source` | (required) | Stream URL, webcam index (`0`) or video file |
| `--demo` | off | Prototype mode with COCO and a substitute object |
| `--require` | `backpack` | Demo: COCO object(s) standing in for PPE |
| `--model` | demo: `yolov8n.pt` | YOLO weights |
| `--conf` | `0.4` | Minimum detection confidence |
| `--imgsz` | `640` | Inference size; `320` is much faster on the Pi but less accurate |
| `--save-dir` | none | Save the annotated frame of each alert here |
| `--cooldown` | `10` | Seconds between alerts for the same person |
| `--show` | off | Show a window (laptop with a desktop and GUI OpenCV) |
| `--web` | off | Live web viewer |
| `--web-host` | Tailscale IP | Address the viewer listens on |
| `--web-port` | `8082` | Viewer port (8081 is the simulator, 81 the ESP32-CAM) |
| `--db` | off | Write alerts and the live state to the lab-monitor database |
| `--db-map` | none | Demo: which database item (`glasses`, `coat`) each substitute object stands for |
| `--window` | `15` | Frames in the majority vote window |
| `--min-samples` | `5` | Samples needed before deciding |
| `--stats-interval` | `5` | Seconds between stats lines |

If the model lacks a needed class, the script stops at startup and lists the classes it has.

## How it decides

1. **Newest frame only:** a thread reads the stream and keeps only the latest frame, so slow inference never piles up old frames. It reconnects by itself if the stream drops.
2. **Tracking:** `model.track(..., persist=True)` (ByteTrack) gives each person an ID.
3. **Association:** each PPE detection belongs to at most one person.
4. **Smoothing:** per person and item, a majority vote over the last 15 frames, with no decision before 5 samples. One bad frame does not trigger an alert.
5. **Verdict per person:** `OK`, `MISSING` (with the list of missing items), `UNVERIFIABLE` or `PENDING` (not enough frames yet).
6. **`on_violation(person_id, missing, frame)`** in `sentry/output.py`: prints and saves the photo; with `--db` the alert is also recorded in the database. This is where the alarm will be connected later.

## Tests

The association, smoothing, class and cooldown logic is separate from OpenCV/YOLO and is tested without a camera or a model:

```bash
pip install -r requirements-dev.txt
pytest
```
