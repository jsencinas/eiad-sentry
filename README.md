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
| `frame_ingest.py`, `sentry/ingest.py` | Receives frames pushed by the ESP32-CAM over HTTPS and re-serves them as local MJPEG |
| `firmware/` | ESP32-CAM firmware that pushes frames to the Pi ([guide](firmware/README.md)) |
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

## Camera on another network

The ESP32-CAM and the Pi are on different networks, and the ESP32 cannot run Tailscale. So the camera pushes its frames over HTTPS to a Tailscale Funnel URL of the Pi, and `frame_ingest.py` hands them to the detector as a local stream:

```bash
python frame_ingest.py --token-file ~/.sentry_token          # receives the camera's frames
python ppe_detector.py --demo --source http://127.0.0.1:8084/stream --save-dir out --web
```

Setup, flashing and security are in the [firmware guide](firmware/README.md). `tools/push_frames.py` does the same from the laptop webcam, to test the path before flashing the camera.

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

## Arguments

```
python ppe_detector.py --source SOURCE [--demo] [--require OBJECTS] [--model WEIGHTS]
    [--conf 0.4] [--imgsz 640] [--save-dir out] [--cooldown 10] [--show]
    [--web] [--web-host HOST] [--web-port 8082]
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
6. **`on_violation(person_id, missing, frame)`** in `sentry/output.py`: today it prints and saves the photo. This is where the alarm and MySQL will be connected later.

## Tests

The association, smoothing, class and cooldown logic is separate from OpenCV/YOLO and is tested without a camera or a model:

```bash
pip install -r requirements-dev.txt
pytest
```
