# Desktop dependency notices

The desktop preview bundles Python, PyInstaller's bootloader, and the libraries
declared in `backend/requirements.txt` and `requirements-desktop.txt`. Package
license files and metadata are retained where provided by their distributions.
The build writes an exact installed dependency list alongside the installer.

Python: https://docs.python.org/3/license.html
PyInstaller bootloader exception: https://pyinstaller.org/en/stable/license.html
Pystray (LGPL-3.0): https://github.com/moses-palmer/pystray/blob/master/COPYING.LGPL
pywebview (BSD-3-Clause): https://github.com/r0x0r/pywebview/blob/master/LICENSE
Python.NET (MIT, Windows native window bridge): https://github.com/pythonnet/pythonnet/blob/master/LICENSE
Pillow (HPND): https://github.com/python-pillow/Pillow/blob/main/LICENSE
OpenCV: https://github.com/opencv/opencv/blob/4.x/LICENSE
NumPy: https://github.com/numpy/numpy/blob/main/LICENSE.txt
Google Gen AI SDK: https://github.com/googleapis/python-genai/blob/main/LICENSE

FFmpeg and FFprobe are **not bundled** in this preview. Users install them
separately. A release bundling these tools needs a reviewed redistributable build,
its licenses, and corresponding source for the build and its linked dependencies.
See https://ffmpeg.org/legal.html. Merely copying a locally installed executable
and linking to generic FFmpeg source is insufficient for a release checklist.

Before public release, generate a complete software bill of materials and audit
the actual frozen bundle's notices and licenses, then sign the installer/app.

The Resolve integration's `WorkflowIntegration.node` is **not redistributed**.
Its installation helper copies the matching bridge from the user's own installed
Blackmagic Developer SDK. Resolve supplies the plugin's Electron runtime. Use the
installed Resolve SDK documentation and license for that vendor component.
