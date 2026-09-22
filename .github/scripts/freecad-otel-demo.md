# FreeCAD local collector demo

The Ubuntu workflow enables `extras=otel`. Only the `TestCoinNodeSnapshots` Python test step
sends spans and failure logs directly through the runner's local collector at
`http://127.0.0.1:4318`. The RunsOn stack must configure a remote backend with
traces and logs enabled. The stack's GitHub App needs access to this repository.
No backend credentials are needed in the workflow.

## Direct Python instrumentation

The workflow installs the pinned Python OTel SDK and OTLP/HTTP exporter into
`$RUNNER_TEMP/otel-python`. The snapshot test step sets
`FREECAD_OTEL_PYTHONPATH`; the opt-in runner adds that directory to `sys.path`
explicitly because FreeCAD ignores `PYTHONPATH` during Python initialization. This assumes the Ubuntu
build uses the same system Python ABI as `python3`; verify on the first CI run.

`FREECAD_TEST_OTEL=true` activates `TelemetryTestRunner.py`, packaged alongside
FreeCAD's TestApp module for both build-directory and installed-app testing.
Without this flag, the standard unittest runner is used and no OTel imports occur.
`FREECAD_TEST_GROUP` is `GUI snapshots / TestCoinNodeSnapshots / build`.
This selects seven test methods in the snapshot module for instrumentation. The
other CLI and GUI steps still run normally with telemetry disabled. Individual
spans retain their full test IDs, distinguishing the test methods inside the group.
If snapshot testing is disabled, this workflow produces no custom Python test spans.

The runner starts a group span, then an active span for each test. Assertion failures,
exceptions, and failed subtests mark the test span as Error and emit a log containing
the actual traceback with that span's trace/span IDs. Expected failures are not
errors; unexpected successes are. Skipped tests get spans labeled `test.status=skipped`.
Fixture failures get diagnostic spans at error-report time, not fixture-duration spans.
Normal console output and unittest outcomes are preserved. Arbitrary stdout is not
exported as logs. Child operations require additional instrumentation.

The SDK batches telemetry during execution and flushes/shuts down both providers
before the test runner returns. A hard kill can lose pending telemetry or unfinished
spans. Exporter errors are reported by the SDK and do not prove test failures.
Python no longer writes intermediate JSON telemetry reports.

## C++ results

C++ tests still run and produce their existing JSON reports, but the workflow does
not export them as telemetry. The demo uses direct Python instrumentation only.
The workflow waits 15 seconds for the local collector and uploads the report directory.

## Recording

1. Run **Build Ubuntu 24.04** from the branch with these changes.
2. In SigNoz, filter `service.name = freecad-tests` and `github.run.id` to this run.
3. For the direct instrumentation demo, filter `telemetry.source = unittest-direct`.
4. Open `GUI snapshots / TestCoinNodeSnapshots / build` and inspect its test spans.
5. For a failed test, open its Logs tab for its assertion/exception traceback.

No failure is injected. A passing run has no failure logs. Prepare a failing test
on a demo branch for a repeatable failure walkthrough. Workflow steps skipped after
an earlier failure remain skipped; telemetry does not change test execution conditions.
Runner CPU/memory metrics describe the host, not individual test resource consumption.

The group spans inherit RunsOn's TRACEPARENT when available, attaching to the runner
runtime context, not synthetic GitHub step spans. Without it, each invocation starts
a new trace discoverable by run ID. Telemetry uses SDK providers local to the test
runner and does not replace an application's global OTel providers.

## Local validation

Install `opentelemetry-sdk==1.37.0` and
`opentelemetry-exporter-otlp-proto-http==1.37.0` in a virtual environment, then run:

```sh
python .github/scripts/test_python_telemetry.py
```

A full FreeCAD CI run is required to verify embedded-Python imports and SigNoz delivery.
