"""Install, upgrade and uninstall only on an explicitly disposable Windows machine."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer.launcher.build import LAUNCHER
from installer.launcher.smoke import FAKE_BROWSER, assert_stopped, cleanup_launchers, launch, ready, wait_for
from installer.smoke import isolated_environment


UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{A0352AC4-4E53-4582-84D1-B1D719AA4FD4}_is1"


def authorized(explicit: bool) -> bool:
    return explicit


def standard_user_from_groups(groups: list[tuple[str, int]]) -> bool:
    # A filtered administrator token still carries this SID with SE_GROUP_USE_FOR_DENY_ONLY.
    return all(sid != "S-1-5-32-544" for sid, _attributes in groups)


def standard_user_verified() -> bool:
    import ctypes
    from ctypes import wintypes

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    class TokenGroups(ctypes.Structure):
        _fields_ = [("count", wintypes.DWORD), ("groups", SidAndAttributes * 1)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    token, size = wintypes.HANDLE(), wintypes.DWORD()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        security.GetTokenInformation(token, 2, None, 0, ctypes.byref(size))  # TokenGroups
        if ctypes.get_last_error() != 122:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 2, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        count = TokenGroups.from_buffer(buffer).count
        groups = (SidAndAttributes * count).from_buffer(buffer, TokenGroups.groups.offset)
        memberships = []
        for group in groups:
            text = ctypes.c_void_p()
            if not security.ConvertSidToStringSidW(group.sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if text.value is None:
                    raise OSError("Token group returned no SID")
                memberships.append((ctypes.wstring_at(text.value), group.attributes))
            finally:
                kernel.LocalFree(text)
        return standard_user_from_groups(memberships)
    finally:
        kernel.CloseHandle(token)


def registered() -> bool:
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view):
                    return True
            except FileNotFoundError:
                pass
    return False


def shortcuts() -> list[Path]:
    import ctypes

    result = []
    for folder in (2, 16):  # Per-user Programs and Desktop, including redirected folders.
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, folder, None, 0, buffer) != 0:
            raise OSError("Could not resolve the per-user shortcut folder")
        result.append(Path(buffer.value) / "Cutgraph Studio.lnk")
    return result


def no_existing_install() -> None:
    if registered() or any(path.exists() for path in shortcuts()) or (
        Path(os.environ["LOCALAPPDATA"]) / "Programs/Cutgraph Studio"
    ).exists():
        raise ValueError("Studio is already present here. Use a clean disposable machine.")


def wait_uninstalled(install: Path) -> None:
    # Inno's loader returns before its temporary second phase finishes. The directory
    # and final registry-key removal jointly gate assertions and the next installation.
    wait_for(lambda: not registered() and not install.exists(), "complete two-phase uninstall", timeout=180)
    if any(path.exists() for path in shortcuts()):
        raise RuntimeError("Uninstall left a Studio shortcut")


def invoke(executable: Path, arguments: list[str], env: dict[str, str]) -> None:
    subprocess.run([str(executable), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", *arguments],
                   env=env, check=True, timeout=300)


def smoke(setup: Path, temp_root: Path, *, disposable: bool = False) -> dict:
    from installer.build import SOURCE, outside_checkout

    if not authorized(disposable):
        raise ValueError("Disposable-machine authorization must be checked before starting the lifecycle smoke")
    no_existing_install()
    temp_root = outside_checkout(temp_root, SOURCE)
    with tempfile.TemporaryDirectory(prefix="installer-lifecycle-", dir=temp_root) as temporary:
        work = Path(temporary)
        install, state = work / "program", work / "state"
        state.mkdir()
        env = isolated_environment(state)
        env["CUTGRAPH_LAUNCHER_BROWSER"] = str(install / "python/python.exe")
        fake = work / "browser.py"
        fake.write_text(FAKE_BROWSER, encoding="utf-8")
        env["CUTGRAPH_LAUNCHER_BROWSER_ARGS"] = json.dumps([str(fake), "{url}"])
        preserved = {}
        for relative in ("workspaces/preserved.txt", "outputs/projects/preserved.txt", "runtime/preserved.txt",
                         "data/source/preserved.txt", "addons/synthetic/preserved.txt"):
            path = state / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic lifecycle fixture")
            preserved[relative] = path.read_bytes()

        def check_data(*, removed_addons: bool = False):
            if not state.is_dir():
                raise RuntimeError("Uninstall removed the state home")
            for relative, contents in preserved.items():
                if removed_addons and relative.startswith("addons/"):
                    if (state / "addons").exists():
                        raise RuntimeError("The explicit add-on removal option left downloaded add-ons")
                elif (state / relative).read_bytes() != contents:
                    raise RuntimeError("Install lifecycle changed user data: " + relative)

        install_args = ["/CURRENTUSER", f"/DIR={install}", "/MERGETASKS=!desktopicon"]
        exe = install / LAUNCHER
        processes = []
        observed = []
        try:
            invoke(setup, [*install_args, f"/LOG={work / 'install.log'}"], env)
            if not exe.is_file():
                raise RuntimeError("The Start-menu shortcut target is missing")
            # Resolve the actual per-user shortcut target.
            command = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + \
                "[Environment]::GetFolderPath('Programs')+'\\Cutgraph Studio.lnk'); $s.TargetPath"
            target = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                    capture_output=True, text=True, env=env, check=True, timeout=30).stdout.strip()
            if Path(target).resolve() != exe.resolve():
                raise RuntimeError("The Start-menu shortcut does not resolve to the installed launcher")
            process = launch(exe, env)
            processes.append(process)
            info, browser = ready(state, process)
            pids = [process.pid, info["serverPid"], browser["pid"], browser["child"]]
            observed.append((pids, info["port"]))
            stale = install / "stale-program-file.txt"
            stale.write_bytes(b"obsolete program file")
            stale_dir = install / "obsolete-directory"
            stale_dir.mkdir()
            (stale_dir / "obsolete.txt").write_bytes(b"obsolete")
            invoke(setup, [*install_args, f"/LOG={work / 'upgrade.log'}"], env)
            process.wait(timeout=30)
            assert_stopped(pids, info["port"])
            if stale.exists() or stale_dir.exists():
                raise RuntimeError("Upgrade retained stale program files")
            check_data()
            (state / "browser.json").unlink()
            process = launch(exe, env)
            processes.append(process)
            info, browser = ready(state, process)
            pids = [process.pid, info["serverPid"], browser["pid"], browser["child"]]
            observed.append((pids, info["port"]))
            invoke(install / "unins000.exe", [f"/LOG={work / 'uninstall.log'}"], env)
            process.wait(timeout=30)
            assert_stopped(pids, info["port"])
            wait_uninstalled(install)
            check_data()
            invoke(setup, install_args, env)
            invoke(install / "unins000.exe", ["/REMOVEADDONS=1"], env)
            wait_uninstalled(install)
            check_data(removed_addons=True)
            no_existing_install()
        finally:
            cleanup_launchers(processes, observed, sys.exception())

    return {"install": "passed", "upgrade": "passed", "uninstall": "passed", "preservedState": True,
            "standardUserVerified": standard_user_verified(),
            "staleFilesRemoved": True, "explicitAddonRemoval": True, "zeroProcessesAndListeners": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("setup", type=Path)
    parser.add_argument("--temp-root", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--i-am-on-a-disposable-machine", action="store_true")
    args = parser.parse_args()
    if not authorized(args.i_am_on_a_disposable_machine):
        print("Refusing to install: pass --i-am-on-a-disposable-machine on a disposable Windows machine.", file=sys.stderr)
        return 2
    if sys.platform != "win32":
        print("The installer lifecycle smoke requires Windows.", file=sys.stderr)
        return 2
    try:
        print(json.dumps(smoke(args.setup.resolve(), args.temp_root,
                               disposable=args.i_am_on_a_disposable_machine), indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Installer lifecycle smoke failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
