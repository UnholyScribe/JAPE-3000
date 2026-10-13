"""JAPE 3000 - the Jagged Alliance 3 Person Editor. Desktop entry point.

Starts the local API on 127.0.0.1 and opens it in a native window (Edge
WebView2). If no webview runtime is available it falls back to the default
browser, so the app still works everywhere.
"""
import os
import sys
import threading
import webbrowser

if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
    sys.path.insert(0, sys._MEIPASS)                      # noqa: SLF001

import server                                             # noqa: E402

# The window is created once, before any save is open, so it cannot name a
# game without risking naming the wrong one - it read "Jagged Alliance 3
# Person Editor" above a JA2 save. The editor sets the page title to the
# loaded save's own game once it knows; this is just the product name.
TITLE = 'JAPE 3000'


def only_one_instance():
    """Refuse to start twice, and raise the existing window instead.

    A named mutex, not a lock file: Windows releases it when the process ends,
    however it ends. A PID file survives a crash and then refuses to let the app
    start at all, which is a worse failure than the one being fixed.

    **A test build and the trusted build may still run side by side.** They
    already keep separate settings for that reason, so the name carries the
    build kind - otherwise the copy in dist/ would refuse to open while the real
    one was running, which is exactly when you want both.

    Returns True to carry on, False when another instance already holds it.
    Never raises: off Windows, or if the call is unavailable, it allows the
    launch. A single-instance guard is a convenience and must never be the
    reason the editor will not open.
    """
    if os.name != 'nt':
        return True
    try:
        import ctypes
        import gamepaths
        kind = 'test' if gamepaths.is_test_build() else 'main'
        # use_last_error matters: ctypes.get_last_error() is only populated for
        # a DLL loaded that way, and the whole check rests on reading
        # ERROR_ALREADY_EXISTS correctly.
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = k32.CreateMutexW(None, False, 'Local\\JAPE3000-' + kind)
        err = ctypes.get_last_error()
        if not handle:
            return True
        if err == 183:                        # ERROR_ALREADY_EXISTS
            _focus_existing()
            return False
        globals()['_INSTANCE_LOCK'] = handle  # hold it for the process lifetime
        return True
    except Exception:                                          # noqa: BLE001
        return True


def _focus_existing():
    """Raise the window that is already open, so the click is not ignored.

    Refusing in silence looks identical to the app failing to start, which has
    someone double-clicking it again - the very thing being prevented.
    """
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, TITLE)
        if hwnd:
            u.ShowWindow(hwnd, 9)             # SW_RESTORE, in case it is minimised
            u.SetForegroundWindow(hwnd)
    except Exception:                                          # noqa: BLE001
        pass


def main():
    if '--cli' in sys.argv:
        _console_for_cli()
        import ja3_cli
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != '--cli']
        try:
            code = ja3_cli.main()
        except SystemExit:
            raise
        except Exception:                                  # noqa: BLE001
            import traceback
            traceback.print_exc()
            code = 1
        sys.exit(code)

    # After the CLI branch on purpose: a command line run is not a second
    # window and must not be blocked by one.
    if not only_one_instance():
        print(TITLE + ' is already running - bringing that window to the front.')
        return 0

    httpd, url = server.start()

    # Runs that were ended rather than closed leave their unpack folder in
    # %TEMP% for good. Swept in the background so startup does not wait.
    if getattr(sys, 'frozen', False):
        import removal
        threading.Thread(target=removal.sweep_stale_temp, daemon=True).start()

    if '--browser' in sys.argv:
        print(TITLE)
        print('  ' + url)
        webbrowser.open(url)
        threading.Event().wait()
        return

    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        print('opened %s in your browser (pywebview not installed)' % url)
        threading.Event().wait()
        return

    # The black-window bug, and a switch for it.
    #
    # Diagnosed 2026-09-05 while it was happening: the editor's own server was
    # answering in 19ms, and the identical page rendered correctly in another
    # browser against that same server, at the same moment the window was
    # black. Nothing in this project is at fault - WebView2 loses its drawing
    # surface and does not take it back.
    #
    # Blank WebView2 is very often GPU compositing, and WebView2 reads its
    # extra switches from this environment variable. Off by default, because
    # software rendering is slower and most people never see the bug. Set
    # JAPE_NO_GPU=1 to try it if you do.
    if os.environ.get('JAPE_NO_GPU'):
        prev = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', '')
        os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (
            prev + ' --disable-gpu --disable-gpu-compositing').strip()
        print('JAPE_NO_GPU set: asking WebView2 for software rendering')

    try:
        webview.create_window(TITLE, url, width=1280, height=860,
                              min_size=(980, 640), background_color='#0c0f11')
        webview.start(**_storage())
    except Exception as exc:                               # noqa: BLE001
        print('native window unavailable (%s); falling back to the browser' % exc)
        webbrowser.open(url)
        threading.Event().wait()
    finally:
        httpd.shutdown()
        _finish_removal()


def _console_for_cli():
    """Give --cli somewhere to print.

    The exe is built windowed, so started from a console it has no stdout:
    every command printed nothing, and an error came up as a dialog box. Borrow
    the console it was started from. Output that is already redirected to a
    file or pipe is left alone.
    """
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):    # ATTACH_PARENT_PROCESS
            return
        out = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
        if sys.stdout is None:
            sys.stdout = out
        if sys.stderr is None:
            sys.stderr = out
        print()
    except Exception:                                      # noqa: BLE001
        pass


def _storage():
    """One known cache folder for the window instead of a new random one in
    %TEMP% every launch, which pywebview never deletes. Private mode stays on,
    so nothing from a session is kept in it. If the folder cannot be made,
    fall back to pywebview's default rather than lose the window."""
    import gamepaths
    path = os.path.join(gamepaths.local_data_dir(), 'WebView2')
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return {}
    return {'storage_path': path, 'private_mode': True}


def _finish_removal():
    """After "Remove from this computer", take the window cache too - it could
    not go while the window was using it."""
    try:
        import removal
        for path in removal.PENDING_ON_EXIT:
            removal.clean_cache_on_exit(path)
    except Exception:                                      # noqa: BLE001
        pass


if __name__ == '__main__':
    main()
