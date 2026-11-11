# -*- mode: python ; coding: utf-8 -*-
"""Build recipe for the Schedru app.

    pyinstaller packaging/Schedru.spec --noconfirm

Run on the kind of machine the app is for: on a Mac it produces
dist/Schedru.app for that Mac's processor. (Elsewhere it produces a plain
folder, dist/Schedru/, which is useful for checking that nothing is missing.)
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))

from syllabus_cal.app import APP_NAME, __version__  # noqa: E402

import googleapiclient  # noqa: E402

WEB = ROOT / "syllabus_cal" / "app" / "web"
DISCOVERY = "googleapiclient/discovery_cache/documents"

datas = [
    # The whole interface: pages, fonts, icon, tutorial video.
    (str(WEB), "syllabus_cal/app/web"),
    # google-api-python-client carries a description of every Google API
    # (about 90 MB). Schedru talks to one of them.
    (str(Path(googleapiclient.__file__).parent / "discovery_cache" / "documents" / "calendar.v3.json"), DISCOVERY),
]

analysis = Analysis(
    [str(ROOT / "packaging" / "schedru_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=[],
    # Only the terminal version uses these.
    excludes=["rich", "tkinter", "pytest", "syllabus_cal.confirm", "syllabus_cal.__main__"],
    noarchive=False,
)

# A packaging hook may have pulled in every API description anyway.
analysis.datas = [
    entry
    for entry in analysis.datas
    if DISCOVERY not in entry[0].replace("\\", "/") or entry[0].endswith("calendar.v3.json")
]

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collected = COLLECT(executable, analysis.binaries, analysis.datas, name=APP_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        collected,
        name=f"{APP_NAME}.app",
        icon=str(ROOT / "packaging" / "Schedru.icns"),
        bundle_identifier="com.schedru.app",
        version=__version__,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # follow light/dark
            "NSHumanReadableCopyright": "Schedru",
            # The window loads its pages from a server inside the app itself.
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
