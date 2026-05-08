# Recovery hooks

Three hook kinds ship; all implement the `RecoveryHook` Protocol
(`kind: str`, `async def run() -> RecoveryResult`).

## bash

```yaml
recovery:
  kind: bash
  script: scripts/restart_api.sh
  args: ["api"]
  timeout_seconds: 60
```

The script must be **executable** at process start (the hook checks
`os.access(script, os.X_OK)` and records exit code 126 if not). On
timeout the child is killed and the hook records exit code 124.

stdout/stderr are captured fully, then truncated to 4 KiB each before
they hit the audit table.

## systemctl

```yaml
recovery:
  kind: systemctl
  unit: my-api.service
  timeout_seconds: 60
```

Runs `systemctl restart <unit>`. On macOS (or any host without
`systemctl` on `$PATH`), the hook records exit code 126 with a clear
message. The launchctl alternative for macOS development:

```bash
launchctl kickstart -k gui/$(id -u)/com.example.my-api
```

Wrap that in a bash hook if you want it triggered automatically on a
Mac.

## noop

```yaml
recovery:
  kind: noop
```

Logs the firing and records the audit row, but runs no side effects.
Good for:

- dry-running new YAML configs against a real fleet
- the `simulate` CLI command
- staging endpoints where you want streak detection but no auto-restart

## Adding a new hook

1. Create `src/health_monitor/recovery/<kind>.py` with a class that
   matches the Protocol.
2. Add a new value to `RecoveryKind` in `config.py`.
3. Wire it into `build_hook()` in `recovery/__init__.py`.
4. Tests live next to the existing hooks; mock `subprocess` rather
   than running real services.
