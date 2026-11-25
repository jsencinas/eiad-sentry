"""Receive JPEG frames pushed by the ESP32-CAM and re-serve them as local MJPEG.

See sentry/ingest.py and the "ESP32-CAM on another network" section of the README.
"""

from sentry.ingest import main

if __name__ == "__main__":
    main()
