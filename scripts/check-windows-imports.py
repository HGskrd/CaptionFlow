"""Report missing Windows loader imports before the GPUI event loop can start."""
import ctypes as C
import json
from pathlib import Path
import sys
import pefile
kernel=C.WinDLL('kernel32',use_last_error=True)
kernel.GetProcAddress.argtypes=[C.c_void_p,C.c_char_p];kernel.GetProcAddress.restype=C.c_void_p
kernel.GetModuleFileNameW.argtypes=[C.c_void_p,C.c_wchar_p,C.c_uint32]
result=[]
for item in pefile.PE(sys.argv[1]).DIRECTORY_ENTRY_IMPORT:
    name=item.dll.decode()
    try:
        library=C.WinDLL(name)
        path=C.create_unicode_buffer(32768);kernel.GetModuleFileNameW(library._handle,path,len(path))
        missing=[symbol.name.decode() for symbol in item.imports if symbol.name and not kernel.GetProcAddress(library._handle,symbol.name)]
        result.append(dict(dll=name,path=path.value,missing=missing))
    except OSError as error:result.append(dict(dll=name,error=str(error)))
Path('dist/imports.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
