#!/usr/bin/env python3
"""Export Google Test JSON timings and assertion failures via local OTLP/protobuf."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.request import Request, urlopen

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest, ExportLogsServiceResponse


def attributes(target, values):
    for key, value in values.items():
        item = target.add(key=key)
        item.value.string_value = str(value)


def timestamp(value):
    # Workflow sets TZ=UTC because Google Test emits local timestamps without offsets.
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def duration(value):
    return int(Decimal(str(value).removesuffix('s')) * 1_000_000_000)


def build_requests(paths):
    traces, logs = ExportTraceServiceRequest(), ExportLogsServiceRequest()
    resource = {
        'service.name': 'freecad-tests',
        'github.repository': os.getenv('GITHUB_REPOSITORY', ''),
        'github.run.id': os.getenv('GITHUB_RUN_ID', ''),
        'github.run.attempt': os.getenv('GITHUB_RUN_ATTEMPT', ''),
        'telemetry.source': 'gtest-json',
    }
    rs = traces.resource_spans.add()
    rl = logs.resource_logs.add()
    attributes(rs.resource.attributes, resource)
    attributes(rl.resource.attributes, resource)
    spans = rs.scope_spans.add()
    records = rl.scope_logs.add()
    spans.scope.name = records.scope.name = 'freecad.gtest-reporter'
    context = re.fullmatch(r'00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})', os.getenv('TRACEPARENT', ''))
    trace_id, parent_id = secrets.token_bytes(16), b''
    if context and int(context[1], 16) and int(context[2], 16):
        trace_id, parent_id = bytes.fromhex(context[1]), bytes.fromhex(context[2])
    for path in paths:
        report = json.loads(path.read_text())
        module_id = secrets.token_bytes(8)
        children = []
        for suite in report.get('testsuites', []):
            for case in suite.get('testsuite', []):
                # Disabled/skipped tests have no executed duration to visualize.
                if case.get('status') != 'RUN' or case.get('result') == 'SKIPPED':
                    continue
                start = timestamp(case['timestamp'])
                end = start + duration(case['time'])
                span = spans.spans.add(
                    trace_id=trace_id, span_id=secrets.token_bytes(8),
                    parent_span_id=module_id, name=f"{suite['name']}.{case['name']}",
                    kind=1, start_time_unix_nano=start, end_time_unix_nano=end,
                )
                labels = {'test.suite': suite['name'], 'test.name': case['name'], 'test.report': path.name}
                failures = case.get('failures', [])
                attributes(span.attributes, {**labels, 'test.status': 'failure' if failures else 'success'})
                span.status.code = 2 if failures else 1
                if failures:
                    span.status.message = 'Google Test assertion failed'
                for failure in failures:
                    record = records.log_records.add(
                        trace_id=trace_id, span_id=span.span_id,
                        time_unix_nano=end, observed_time_unix_nano=time.time_ns(),
                        severity_number=17, severity_text='ERROR',
                    )
                    record.body.string_value = failure['failure']
                    attributes(record.attributes, {**labels, 'event.name': 'test.assertion.failure'})
                children.append(span)
        if children:
            module = spans.spans.add(
                trace_id=trace_id, span_id=module_id, parent_span_id=parent_id,
                name=path.stem.removesuffix('_gtest_results'), kind=1,
                start_time_unix_nano=min(s.start_time_unix_nano for s in children),
                end_time_unix_nano=max(s.end_time_unix_nano for s in children),
            )
            module.status.code = 2 if any(s.status.code == 2 for s in children) else 1
    return traces, logs


def send(endpoint, signal, payload, response_type):
    request = Request(endpoint.rstrip('/') + '/v1/' + signal,
                      data=payload.SerializeToString(),
                      headers={'Content-Type': 'application/x-protobuf'}, method='POST')
    with urlopen(request, timeout=30) as response:
        result = response_type.FromString(response.read())
    if result.HasField('partial_success') and result.partial_success.ByteSize():
        raise RuntimeError(f'Collector reported partial success for {signal}: {result.partial_success}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report_dir', type=Path)
    args = parser.parse_args()
    paths = sorted(args.report_dir.glob('*_gtest_results.json'))
    if not paths:
        print('No Google Test JSON reports found; no test telemetry exported.')
        return
    traces, logs = build_requests(paths)
    endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://127.0.0.1:4318')
    span_count = len(traces.resource_spans[0].scope_spans[0].spans)
    log_count = len(logs.resource_logs[0].scope_logs[0].log_records)
    if span_count:
        send(endpoint, 'traces', traces, ExportTraceServiceResponse)
    if log_count:
        send(endpoint, 'logs', logs, ExportLogsServiceResponse)
    print(f'Local collector accepted {span_count} spans and {log_count} assertion logs.')


if __name__ == '__main__':
    main()
