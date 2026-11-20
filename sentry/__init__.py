"""Sentry: personal protective equipment (PPE) detection for the EIAD.

Pure logic (association, smoothing, class names, cooldown) lives in modules
that do not import OpenCV, NumPy or Ultralytics, so it can be tested anywhere.
"""
