"""An OS file picker for the browser, without opening a second app window."""
import ctypes
from pathlib import Path
import subprocess
import sys
import threading

_picker_lock = threading.Lock()


def _windows_picker():
    from ctypes import wintypes as w

    # OPENFILENAMEW from the Windows SDK commdlg.h (Windows fields only).
    class OpenFileName(ctypes.Structure):
        _fields_ = [('lStructSize', w.DWORD), ('hwndOwner', w.HWND), ('hInstance', w.HINSTANCE),
            ('lpstrFilter', w.LPCWSTR), ('lpstrCustomFilter', w.LPWSTR), ('nMaxCustFilter', w.DWORD),
            ('nFilterIndex', w.DWORD), ('lpstrFile', w.LPWSTR), ('nMaxFile', w.DWORD),
            ('lpstrFileTitle', w.LPWSTR), ('nMaxFileTitle', w.DWORD), ('lpstrInitialDir', w.LPCWSTR),
            ('lpstrTitle', w.LPCWSTR), ('Flags', w.DWORD), ('nFileOffset', w.WORD),
            ('nFileExtension', w.WORD), ('lpstrDefExt', w.LPCWSTR), ('lCustData', ctypes.c_ssize_t),
            ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', w.LPCWSTR), ('pvReserved', ctypes.c_void_p),
            ('dwReserved', w.DWORD), ('FlagsEx', w.DWORD)]

    dialog = ctypes.WinDLL('comdlg32', use_last_error=True)
    dialog.GetOpenFileNameW.argtypes = [ctypes.POINTER(OpenFileName)]
    dialog.GetOpenFileNameW.restype = w.BOOL
    dialog.CommDlgExtendedError.argtypes = []
    dialog.CommDlgExtendedError.restype = w.DWORD
    buffer = ctypes.create_unicode_buffer(65536)
    form = OpenFileName(lStructSize=ctypes.sizeof(OpenFileName), nMaxFile=len(buffer),
        lpstrFile=ctypes.cast(buffer, w.LPWSTR), lpstrTitle='Choose original video — Video Analyzer',
        lpstrFilter='Video media\0*.mp4;*.mov;*.mxf;*.mkv;*.avi;*.webm;*.m4v;*.mpg;*.mpeg;*.mts;*.m2ts;*.braw;*.r3d;*.ari;*.arx;*.crm\0All files\0*.*\0\0',
        nFilterIndex=1,
        # EXPLORER, ALLOWMULTISELECT, FILEMUSTEXIST, PATHMUSTEXIST, NOCHANGEDIR.
        Flags=0x80000 | 0x200 | 0x1000 | 0x800 | 0x8)
    if not dialog.GetOpenFileNameW(ctypes.byref(form)):
        error = dialog.CommDlgExtendedError()
        if error:
            raise OSError(f'The media picker failed (Windows error {error}).')
        return []
    parts = ''.join(buffer).split('\0\0', 1)[0].split('\0')
    return parts if len(parts) == 1 else [str(Path(parts[0]) / name) for name in parts[1:]]


def choose_files():
    if not _picker_lock.acquire(blocking=False):
        raise ValueError('The media picker is already open. Select your files there.')
    try:
        if sys.platform == 'win32':
            return _windows_picker()
        if sys.platform == 'darwin':
            script = '''try
set chosen to choose file with prompt "Choose original video — Video Analyzer" with multiple selections allowed
set resultText to ""
repeat with itemPath in chosen
set resultText to resultText & POSIX path of itemPath & linefeed
end repeat
return resultText
on error number -128
return ""
end try'''
            result = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, text=True)
            if result.returncode:
                raise OSError('The media picker could not open. Use the desktop importer.')
            return [path for path in result.stdout.splitlines() if path]
        raise OSError('Native browser import is available on Windows and macOS. Use the desktop importer.')
    finally:
        _picker_lock.release()


def choose_folders():
    if not _picker_lock.acquire(blocking=False):
        raise ValueError('The media picker is already open. Select your folders there.')
    try:
        if sys.platform == 'win32':
            from app.windows_folder_picker import choose_windows_folders
            return choose_windows_folders()
        if sys.platform == 'darwin':
            script = '''try
set chosen to choose folder with prompt "Choose video folders — Video Analyzer" with multiple selections allowed
set resultText to ""
repeat with itemPath in chosen
set resultText to resultText & POSIX path of itemPath & linefeed
end repeat
return resultText
on error number -128
return ""
end try'''
            result = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, text=True)
            if result.returncode:
                raise OSError('The folder picker could not open. Drop folders into the desktop window instead.')
            return [path for path in result.stdout.splitlines() if path]
        raise OSError('Native folder selection is available on Windows and macOS. Drop folders into the desktop window instead.')
    finally:
        _picker_lock.release()
