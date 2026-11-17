# Windows installation preview

This is the source integration for `0.7.0-windows-preview.6-main-integration`, not a new public Release. Existing v0.7.0 download assets are Mac-only. The earlier preview.5 passed the recorded Windows 10 hardware checks; this new package requires targeted verification after its DSH and installer changes.

## Requirements

- Native Windows desktop applications and a data-capable USB cable with a previously flashed flower. This installer does not install AI applications or flash firmware.
- A separate, maintained Python 3.11+ installation. The preview does not bundle Python. Installation downloads the pinned, SHA256-checked pyserial 3.5 wheel and creates a virtual environment.
- The virtual environment and login entry still depend on the base Python. Do not uninstall or move it. WorkBuddy's bundled interpreter worked in earlier testing, but is not the recommended long-term dependency.

For ordinary consumers the intended delivery is a flower-managed runtime; that packaging is separate work and is not implemented here. Windows 11, multiple flowers and all application versions are not claimed hardware-verified.

## Build and install

Build from this checkout with `python m1/package_windows.py --output dist/windows-preview`. Extract the whole resulting ZIP; do not run entries inside an archive. A Python-equipped developer may also run `python m1/windows_setup.py install` from the checkout on Windows.

Run installation/start operations from Explorer or a terminal you opened independently. Some AI/IDE task hosts reclaim their child processes after a task; a hidden window alone does not establish an independent background lifetime. AI tools can read diagnostics and logs.

1. Install/open the AI application you intend to use. For DSH, open it once to create its desktop profile.
2. In a terminal in the extracted folder, select a source explicitly, for example:

```powershell
py -3 -X utf8 .\app\windows_setup.py install --source codex --dry-run
py -3 -X utf8 .\app\windows_setup.py install --source workbuddy --dry-run
py -3 -X utf8 .\app\windows_setup.py install --source dsh --dry-run
py -3 -X utf8 .\app\windows_setup.py install --source qodercn --dry-run
py -3 -X utf8 .\app\windows_setup.py install --source codex,workbuddy,dsh,qodercn --dry-run
```

3. Restart DSH after installing/updating/removing its plugin. `check.cmd` reports background/source state; an unopened application may be `not_initialized` rather than broken.
4. To connect real hardware, use the same independent Python:

```powershell
py -3 -X utf8 .\app\windows_setup.py install --enable-hardware --port auto
```

`install.cmd` defaults on a first install to Codex + WorkBuddy and dry-run (no USB movement/light). Reinstallation preserves saved source, paths, port and mode unless explicitly overridden. `all` retains the three-source meaning Codex + WorkBuddy + DSH; add Qoder explicitly. `--qoder-root` and `--qoder-app-data` persist custom Qoder locations.

`start.cmd` resumes; `stop.cmd` pauses until start (including across login); `uninstall.cmd` stops the service, removes its login entry and managed DSH registration. Logs, backups and runtime directories are retained for recovery. Installation is per-user under `%LOCALAPPDATA%\RotatingFlower`, with an HKCU Run value named `RotatingFlower`; it starts after that user logs in, not before login as a system service.

## Existing preview users and Python migration

The launcher deliberately reuses an existing flower interpreter when present, to avoid breaking an installation. Installing a separate Python alone does not migrate an old service. To migrate from a WorkBuddy-provided runtime, explicitly run the new Python executable against this package's installer, without changing source/mode settings:

```powershell
& 'C:\path-to-independent-Python\python.exe' -X utf8 .\app\windows_setup.py install
```

Replace the example with the verified actual executable path. This rebuilds the environment and login entry. Verify diagnostics, one real task and login recovery before removing the old interpreter. This specific fresh/migration path still needs Windows acceptance; do not treat a software fixture as proof of a consumer installation.

DSH remains limited in this Windows installer to the default `~/.dsh` desktop profile. Upgrading the existing managed block uses a file URI; configuration receipts preserve original bytes and user edits. The observer keeps main's event retry and temporary-failure recovery, with Windows status paths. Removal preserves a shared observer still referenced by another profile.

## Diagnostics and known limits

- `diagnose.cmd` reports the installed interpreter, the precise HKCU startup key/value it queried, and source state; it does not drive USB. Registry access denial is reported as a query error, not as proof the value is absent.
- Installation steps are recorded in `logs/install-events.jsonl`, including the failing stage, exception type and OS error/path when available. If even the log directory is denied, the original failure still goes to stderr. Keep the terminal output.
- Background logs are in `logs/bridge.log`; windowless startup failures may appear in `logs/startup-error.log` or `runner-error.log`.
- Earlier preview.5 had an unresolved initial WinError 5, followed by a successful unchanged-package installation in an independent terminal. The new diagnostics improve evidence; they do not claim its root cause was fixed. An earlier registry query disagreed with observed successful autostart; the new diagnostic makes future comparison explicit, not a retrospective diagnosis.
- Qoder structured question waits are supported; command-approval waits are not. Sessions with no updates expire after the existing 600-second retention. See [Qoder details](qodercn.md).

## Validation and attribution

The unchanged preview.5 passed [Windows Python 3.11/3.13 CI](https://github.com/dalaofei1154/rotating-flower/actions/runs/44022917649), then reported Win10 single-flower rotation, question-wait light, reboot/login recovery, USB reconnect, stop/start, previous-source regression and Qoder + WorkBuddy overlap checks. Returned package/installed-file hashes matched its baseline. This does not certify preview.6 or every OS/application version.

For this integration: run the existing Mac tests and the Windows/portable workflow against the final source; verify DSH upgrade, user config preservation and task/wait/resume; check the chosen independent-Python installation path. Re-test other hardware paths when affected by actual code changes, not merely because documentation changed.

Each new package includes file hashes, source revision information and [contribution provenance](windows-contributions.md). Old archived packages are not rewritten. Only share a sanitized report, not application databases, full conversations or private machine paths.
