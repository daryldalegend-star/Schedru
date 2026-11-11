"""Schedru: the windowed app on top of the syllabus_cal engine.

    paths/settings   where the key, the Google files and preferences live
    engine           the three outside services (Claude, Google, the screen)
                     behind one seam, with a demo stand-in for tests
    service          what the app can do, as plain method calls
    server           those calls as a local HTTP API, plus the UI's files
    shell            the native window and the global shortcut
"""

__version__ = "1.0.0"

APP_NAME = "Schedru"
TAGLINE = "Your Schedule Optimizer"
