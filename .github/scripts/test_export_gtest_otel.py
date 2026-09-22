import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from export_gtest_otel import build_requests, send
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse


class ExportTests(unittest.TestCase):
    def test_report_timing_failure_and_trace_correlation(self):
        cases = [
            {'name': 'Pass', 'status': 'RUN', 'result': 'COMPLETED', 'timestamp': '2026-09-22T12:00:00', 'time': '0.125s'},
            {'name': 'Fail', 'status': 'RUN', 'result': 'COMPLETED', 'timestamp': '2026-09-22T12:00:01', 'time': '3s',
             'failures': [{'failure': 'part.cpp:12\nExpected: 2\nActual: 3'}]},
            {'name': 'Disabled', 'status': 'NOTRUN'},
            {'name': 'Skipped', 'status': 'RUN', 'result': 'SKIPPED'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'part_gtest_results.json'
            path.write_text(json.dumps({'testsuites': [{'name': 'Part', 'testsuite': cases}]}))
            with patch.dict(os.environ, {'TRACEPARENT': '00-' + 'ab'*16 + '-' + 'cd'*8 + '-01'}):
                traces, logs = build_requests([path])
        spans = traces.resource_spans[0].scope_spans[0].spans
        self.assertEqual(len(spans), 3)
        self.assertEqual(spans[0].end_time_unix_nano - spans[0].start_time_unix_nano, 125000000)
        self.assertEqual(spans[1].status.code, 2)
        self.assertEqual(spans[2].status.code, 2)
        self.assertEqual(spans[2].parent_span_id, bytes.fromhex('cd'*8))
        self.assertEqual(spans[1].parent_span_id, spans[2].span_id)
        log = logs.resource_logs[0].scope_logs[0].log_records[0]
        self.assertEqual(log.trace_id, spans[1].trace_id)
        self.assertEqual(log.span_id, spans[1].span_id)
        self.assertEqual(log.body.string_value, cases[1]['failures'][0]['failure'])

    def test_wire_encoding_and_partial_rejection(self):
        response = ExportTraceServiceResponse()
        with patch('export_gtest_otel.urlopen') as post:
            post.return_value.__enter__.return_value.read.return_value = response.SerializeToString()
            send('http://127.0.0.1:4318', 'traces', ExportTraceServiceRequest(), ExportTraceServiceResponse)
            request = post.call_args.args[0]
            self.assertEqual(request.full_url, 'http://127.0.0.1:4318/v1/traces')
            ExportTraceServiceRequest.FromString(request.data)
            response.partial_success.rejected_spans = 1
            post.return_value.__enter__.return_value.read.return_value = response.SerializeToString()
            with self.assertRaises(RuntimeError):
                send('http://127.0.0.1:4318', 'traces', ExportTraceServiceRequest(), ExportTraceServiceResponse)


if __name__ == '__main__':
    unittest.main()
