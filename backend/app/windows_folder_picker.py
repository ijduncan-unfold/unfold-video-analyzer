"""Windows IFileOpenDialog with native multiple-folder selection."""
import ctypes
from uuid import UUID

HRESULT = ctypes.c_int32
POINTER = ctypes.c_void_p
DWORD = ctypes.c_uint32
FOLDER_OPTIONS = 0x20 | 0x200 | 0x40 | 0x800 | 0x8  # PICKFOLDERS, MULTISELECT, FILESYSTEM, PATHMUSTEXIST, NOCHANGEDIR


class GUID(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]

    @classmethod
    def from_text(cls, text):
        return cls((ctypes.c_ubyte * 16).from_buffer_copy(UUID(text).bytes_le))


def _method(pointer, index, result_type, *argument_types):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(POINTER))).contents
    return ctypes.WINFUNCTYPE(result_type, POINTER, *argument_types)(vtable[index])


def _check(result):
    if result < 0:
        raise OSError(f'The folder picker failed (Windows error 0x{result & 0xffffffff:08X}).')


def choose_windows_folders():
    ole = ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [POINTER, DWORD]
    ole.CoInitializeEx.restype = HRESULT
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), POINTER, DWORD, ctypes.POINTER(GUID), ctypes.POINTER(POINTER)]
    ole.CoCreateInstance.restype = HRESULT
    ole.CoTaskMemFree.argtypes = [POINTER]
    ole.CoTaskMemFree.restype = None
    _check(ole.CoInitializeEx(None, 2))  # Apartment-threaded COM on the picker thread.
    dialog, results = POINTER(), POINTER()
    try:
        clsid = GUID.from_text('DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7')
        iid = GUID.from_text('D57C7288-D4AD-4768-BE02-9D969532D960')
        _check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(dialog)))
        options = DWORD()
        _check(_method(dialog, 10, HRESULT, ctypes.POINTER(DWORD))(dialog, ctypes.byref(options)))
        _check(_method(dialog, 9, HRESULT, DWORD)(dialog, options.value | FOLDER_OPTIONS))
        _check(_method(dialog, 17, HRESULT, ctypes.c_wchar_p)(dialog, 'Choose video folders — Video Analyzer'))
        result = _method(dialog, 3, HRESULT, POINTER)(dialog, None)
        if result & 0xffffffff == 0x800704C7:  # User cancelled; no import.
            return []
        _check(result)
        _check(_method(dialog, 27, HRESULT, ctypes.POINTER(POINTER))(dialog, ctypes.byref(results)))
        count = DWORD()
        _check(_method(results, 7, HRESULT, ctypes.POINTER(DWORD))(results, ctypes.byref(count)))
        paths = []
        for index in range(count.value):
            item, name = POINTER(), POINTER()
            try:
                _check(_method(results, 8, HRESULT, DWORD, ctypes.POINTER(POINTER))(results, index, ctypes.byref(item)))
                _check(_method(item, 5, HRESULT, DWORD, ctypes.POINTER(POINTER))(item, 0x80058000, ctypes.byref(name)))
                paths.append(ctypes.wstring_at(name))
            finally:
                if name:
                    ole.CoTaskMemFree(name)
                if item:
                    _method(item, 2, DWORD)(item)
        return paths
    finally:
        if results:
            _method(results, 2, DWORD)(results)
        if dialog:
            _method(dialog, 2, DWORD)(dialog)
        ole.CoUninitialize()
