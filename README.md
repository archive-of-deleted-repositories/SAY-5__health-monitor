# health-monitor

A Python service that polls backend HTTP/TCP endpoints, tracks uptime and
response latency, fires Bash recovery hooks when an endpoint fails three
consecutive checks, and writes every recovery action to a SQLite audit
table you can query from the CLI.

## What this studies

- **Failure-streak detection.** The trigger is *three consecutive failures*,
  not "three failures in a five-minute window". Rate-based thresholds
  fire on transient blips; consecutive-failure thresholds only fire when
  a service is genuinely down right now. The counter resets to zero on
  any successful check, so a single recovery never re-fires until three
  more consecutive failures occur.
- **Recovery dispatch as a Protocol pattern.** Each hook (`bash`,
  `systemctl`, `noop`) implements the `RecoveryHook` Protocol. Adding a
  new hook (e.g. `kubectl rollout restart`) means writing one class, not
  threading a new branch through the dispatcher.
- **The audit log is load-bearing.** Operators need to be able to answer
  "what restarted, when, why, and what did the script print?" months
  after the fact. Every recovery firing writes a durable row to
  `recovery_actions` with the endpoint, fired-at timestamp, action kind,
  arguments, exit code, truncated stdout/stderr, and duration.

## Modules

| Module | Responsibility |
|---|---|
| `health_monitor.cli` | Click commands: `run`, `status`, `recoveries`, `add-endpoint`, `simulate` |
| `health_monitor.poller` | Async HTTP/TCP probes; per-endpoint polling task |
| `health_monitor.streak` | Per-endpoint consecutive-failure counter |
| `health_monitor.recovery` | `RecoveryHook` Protocol + `RecoveryDispatcher` |
| `health_monitor.recovery.bash` | Subprocess invocation with timeout + capture |
| `health_monitor.recovery.systemctl` | `systemctl restart <unit>` (Linux) |
| `health_monitor.recovery.noop` | Logs would-be action, useful for dry-run |
| `health_monitor.store` | SQLAlchemy schema for `endpoints`, `health_checks`, `recovery_actions` |
| `health_monitor.config` | Pydantic models + YAML loader |
| `health_monitor.obs.logs` | structlog configuration |

## Architecture

```
                      +-----------------------+
                      |   monitor.yaml        |
                      +-----------+-----------+
                                  |
                                  v
+----------+        +-----------------+        +-------------+
| poller   | <----> | StreakDetector  | -----> | Dispatcher  |
| (asyncio)|        | (per-endpoint)  |        |  (Protocol) |
+----+-----+        +-----------------+        +------+------+
     |                                                |
     |  HealthCheck rows                              |  RecoveryAction rows
     v                                                v
+--------------------------------------------------------+
|                       SQLite                           |
|   endpoints  /  health_checks  /  recovery_actions     |
+--------------------------------------------------------+
```

## Quickstart

```bash
make dev
make migrate            # creates tables in sqlite:///health_monitor.db
make up                 # docker compose: monitor + 3 mock backends
```

Or, without docker:

```bash
make dev
.venv/bin/health-monitor run --config config/monitor.example.yaml
```

## Sample CLI output

```
$ health-monitor status --since 1h
endpoint                  checks  pass   uptime%    p95 ms
----------------------------------------------------------
healthy-api                  120   120     100.0         9
flaky-api                    120    61      50.83        4
broken-api                   120     0       0.0         5

$ health-monitor recoveries --since 1h
fired_at                    endpoint            kind          exit      ms
--------------------------------------------------------------------------
2026-05-02T18:12:09+00:00   broken-api          bash             0      31
2026-05-02T18:12:15+00:00   broken-api          bash             0      29
```

## Configuration

```yaml
# config/monitor.example.yaml
db_url: "sqlite:///health_monitor.db"
endpoints:
  - name: api
    url: "http://localhost:8080/healthz"
    kind: http                    # http | tcp
    failure_threshold: 3
    check_interval_s: 10
    timeout_s: 5
    recovery:
      kind: bash                  # bash | systemctl | noop
      script: scripts/restart_api.sh
      args: ["api"]
      timeout_seconds: 60
```

`tcp` endpoints accept `tcp://host:port` or plain `host:port`.

## Testing

```bash
make test            # unit tests with 80% coverage gate
make test-int        # 3-backend integration smoke (gated RUN_INTEGRATION=1)
make lint
make typecheck
```

## What this is not

- **Not an incident-management system.** No PagerDuty / Opsgenie / Slack
  integration. The audit log is the interface; wire your alerting tool
  to it externally.
- **Not a metrics store.** Latencies live in SQLite for the configured
  retention window and roll off. Production should ship them to
  Prometheus or similar.
- **Not a service-discovery tool.** Endpoints are static YAML; no
  Kubernetes API integration, no Consul, no DNS-SD.
- **Not Windows-supported.** Linux first; macOS works for development
  but the `systemctl` hook is unavailable there (use `launchctl`
  manually or wrap it with a bash hook).
- **Not clustered.** Single-process; if you run two copies you will
  get two recoveries per failure window.
- **Not a notifier.** Logs are the only side channel; Slack/email is
  out of scope.

## License

MIT - see [LICENSE](LICENSE).
