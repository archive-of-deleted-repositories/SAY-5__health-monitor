# Multi-stage build for the health-monitor service.
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md /build/
COPY src /build/src
RUN python -m pip install --upgrade pip wheel \
    && pip wheel --no-deps --wheel-dir /build/wheels .

FROM python:${PYTHON_VERSION}-slim AS runtime
RUN useradd --uid 1001 --create-home --shell /bin/bash monitor
WORKDIR /app
COPY --from=builder /build/wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels
COPY config /app/config
COPY scripts /app/scripts
RUN chmod +x /app/scripts/*.sh
USER monitor
ENV HEALTH_MONITOR_LOG_LEVEL=INFO
ENTRYPOINT ["health-monitor"]
CMD ["run", "--config", "/app/config/monitor.example.yaml"]
