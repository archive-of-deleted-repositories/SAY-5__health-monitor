# Recovery scripts

Drop bash scripts here, mark them executable, and reference them from
your monitor YAML:

```yaml
endpoints:
  - name: api
    url: http://localhost:8080/healthz
    failure_threshold: 3
    recovery:
      kind: bash
      script: scripts/restart_api.sh
      timeout_seconds: 60
```

Each script:

- must be executable (`chmod +x`)
- must exit `0` on success and non-zero on failure
- has its stdout and stderr captured (truncated to 4 KiB each) into the
  `recovery_actions` audit table
- is killed if it exceeds `timeout_seconds` (recorded as exit code 124)

The bundled `restart_api.sh` is a placeholder.
