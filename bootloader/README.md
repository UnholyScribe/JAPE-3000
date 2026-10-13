# Custom PyInstaller bootloader (optional)

The released JAPE3000.exe uses a PyInstaller bootloader compiled from the
PyInstaller source rather than the prebuilt one pip ships. The prebuilt
loader is byte-identical across every PyInstaller app, including malware, so
antivirus engines have signatured it; a self-compiled one avoids that false
positive. The code is the same.

The binaries are not in this repository. Without them, `build_app.py` simply
uses the stock loader, and the build works the same.

To make your own (needs MinGW-w64 and the PyInstaller source for the version
you have installed):

    git clone https://github.com/pyinstaller/pyinstaller
    cd pyinstaller/bootloader
    python ./waf all --target-arch=64bit

Copy `run.exe`, `runw.exe`, `run_d.exe` and `runw_d.exe` from
`PyInstaller/bootloader/Windows-64bit-intel/` into
`bootloader/Windows-64bit-intel/` here.
