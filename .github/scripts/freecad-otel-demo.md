# FreeCAD local collector demo

`sub_buildUbuntu.yml` enables `extras=otel` and sends Google Test report telemetry
to the runner's local OTLP/HTTP collector at `http://127.0.0.1:4318`.
The Fleet stack must configure the remote OTLP backend with traces and logs enabled,
and use a runner version supporting the local receiver (RunsOn v3.2.1+).
No backend credentials are needed in this workflow.

## Recording

1. Run Build Ubuntu 24.04 and wait for C++ tests and the export step to finish.
2. In SigNoz Traces Explorer, filter `service.name = freecad-tests` and
   `github.run.id` to the GitHub run ID. Select the matching run attempt as needed.
3. Open a module span and expand individual test spans. Their durations come from
   Google Test reports. Compare test durations to identify the slowest test.
4. If a test actually failed, select its error span and open Logs to see the
   assertion text from Google Test, linked by native trace ID and span ID.
5. Optionally compare the test time window with that runner's CPU and memory.
   Host metrics are not measurements of an individual test's resource usage.

No failures or delays are injected. A successful run has no assertion failure logs.
Prepare a deliberately failing test in a demo branch if you need a repeatable failure.
The exporter sends assertion messages, not arbitrary stdout/stderr or full job logs.
It covers `*_gtest_results.json`; Qt and Python reports are not included.
A crash or timeout that prevents a complete report may leave no test spans.

The exporter preserves TRACEPARENT when available, attaching module spans to the
runner runtime context. It does not attach them to synthetic GitHub step spans.
Without TRACEPARENT, it creates a separate trace discoverable by run ID.
The workflow uses UTC so report timestamps without an offset are interpreted correctly.
Telemetry export is best effort: export failure is visible in its step but does not
change the test result. Local acceptance does not guarantee remote backend delivery.

## Local validation

Install `opentelemetry-proto==1.37.0` in a virtual environment, then run:

```sh
python .github/scripts/test_export_gtest_otel.py
```

Tests use representative Google Test reports and validate timings, skipped tests,
error status, assertion linkage, protobuf encoding, and partial rejection handling.
An end-to-end run on the Fleet runner is still required.
