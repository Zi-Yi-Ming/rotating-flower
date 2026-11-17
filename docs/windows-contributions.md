# Windows integration: contributions and provenance

This integration brings the Windows preview implementation onto main after Qoder CN PR #8. It does not merge Bella-rab's PR #5 wholesale, and it does not change the contents of an existing release.

## Bella-rab — PR #5

Source: [PR #5](https://github.com/dalaofei1154/rotating-flower/pull/5), reviewed snapshot `e698b752c3fe76c1d540a895f874cce3076baf13`.

| Included contribution | Current files | Adaptation |
| --- | --- | --- |
| Explicit SQLite fixture commit/close | `m1/integrated/test_workbuddy_adapter.py`, `test_readiness_recovery.py` | Retains current process mocks and additional assertions |
| Fake-port transport test approach | `m1/integrated/test_integrated_bridge.py` | Adapted to `Flower.transport`, including byte-count writes and short-write handling |
| Four POSIX exclusive-access regression tests | `m1/integrated/test_posix_exclusive.py` | Adapted from `m1/test_posix_exclusive.py` to the current transport; module stand-ins let these branches run on Windows too |
| Package completeness and persistent-loop scenarios | `m1/test_packaged_bridge_runtime.py` | Maintainer implementation extracts actual packages and runs isolated subprocesses; subsequent Qoder lifecycle coverage is a separate maintainer addition |
| Portable CI layout | `.github/workflows/windows-software.yml` | Windows/Linux Python 3.11/3.13 alongside the existing macOS Python 3.9/3.13 workflow; current package paths and Node tests |

The test adaptation commit credits the verified original Git author as a co-author. Source comments and this mapping distinguish adapted code from newly implemented scenarios. The original author identity in the reviewed commit uses the name Luna; the GitHub contributor is Bella-rab.

The current runtime retains its pyserial Windows transport, platform/locking helpers and HKCU Run startup. Bella's alternative Win32 serial backend and scheduled-task installer are not imported. The per-poll file reopen used by our current reader is consistent with the approach Bella described; it is a maintainer implementation in that reader, not a direct copy claimed here.

Our hardware checks of Bella's original control classes confirmed rotation, stopping, lights and reconnection on the tested setup. Those checks do not establish that her complete installer, scheduled-task rollback or all application event paths passed. Her subsequent corrections to earlier platform claims supersede those earlier claims.

## Y1X1n — PR #8

Source: [merged PR #8](https://github.com/dalaofei1154/rotating-flower/pull/8), runtime fixes at `b06b3da016caeb04b00214f3c2745207aa8c0aea`, merged through `a3e6adbf235f4e0eec06e5512daa638047da0f9e`.

`qodercn_adapter.py`, `source_config.py` and `test_qodercn_adapter.py` remain unchanged from the reviewed implementation, including its 39 adapter tests. The original commits are retained in main. Maintainer work supplies Windows wiring, persisted Qoder paths, dependency packaging and integration checks. The original `all` selection remains Codex + WorkBuddy + DSH; Qoder remains opt-in.

## Maintainer Windows work and limits

The Windows installer, IPC, transport integration, DSH file URI/path adaptation, file replacement fixes and later diagnostics are maintainer work. This integration retains main's newer Mac Qoder setup, DSH observer recovery and profile protection instead of replacing those files with older preview copies.

The tested preview.5 ZIP remains archived unchanged (SHA256 `2d382cccd3bfd6780fa6882a5d593667bb2b5a5302b25003555c100ae49266df`). The archived preview.6 changes DSH setup/observer and installer diagnostics. Its own six-job cloud run, DSH upgrade/task checks, independent-Python migration and login-startup checks have now passed within the recorded Windows 10 single-device scope. See the [validation record](windows.md#validation-and-attribution); preview.5 evidence is not automatically a pass for an arbitrary later package.
