import io
import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/Mod/Test'))
from TelemetryTestRunner import text_test_runner
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk._logs.export import InMemoryLogExporter


class DirectTelemetryTests(unittest.TestCase):
    def run_instrumented(self, cases):
        spans, logs = InMemorySpanExporter(), InMemoryLogExporter()
        with patch.dict(os.environ, {
            'FREECAD_TEST_OTEL': 'true', 'FREECAD_TEST_GROUP': 'CLI tests on install',
            'TRACEPARENT': '00-' + 'ab'*16 + '-' + 'cd'*8 + '-01',
        }), patch('opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter', return_value=spans), patch('opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter', return_value=logs):
            stream = io.StringIO()
            result = text_test_runner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(cases))
        return result, spans.get_finished_spans(), [x.log_record for x in logs.get_finished_logs()], stream.getvalue()

    def test_live_context_failures_and_correlated_logs(self):
        active_ids = []
        class Cases(unittest.TestCase):
            def test_pass(self):
                active_ids.append(trace.get_current_span().get_span_context().span_id)
            def test_failure(self):
                self.fail('actual assertion detail')
            def test_error(self):
                raise RuntimeError('actual exception detail')
            def test_subtest(self):
                with self.subTest(value=7):
                    self.fail('subtest detail')
            @unittest.skip('not available')
            def test_skip(self):
                pass
            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail('expected')
            @unittest.expectedFailure
            def test_unexpected_success(self):
                pass
        result, spans, logs, output = self.run_instrumented(Cases)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.testsRun, 7)
        self.assertIn('actual assertion detail', output)
        self.assertEqual(len(spans), 8)
        self.assertEqual(len(logs), 4)
        parent = next(s for s in spans if s.name == 'CLI tests on install')
        self.assertEqual(parent.parent.span_id, int('cd'*8, 16))
        self.assertEqual(parent.context.trace_id, int('ab'*16, 16))
        for span in spans:
            self.assertGreaterEqual(span.end_time, span.start_time)
            if span is not parent:
                self.assertEqual(span.parent.span_id, parent.context.span_id)
        self.assertIn(active_ids[0], [s.context.span_id for s in spans])
        self.assertNotEqual(active_ids[0], 0)
        self.assertFalse(trace.get_current_span().get_span_context().is_valid)
        for log in logs:
            span = next(s for s in spans if s.context.span_id == log.span_id)
            self.assertEqual(span.context.trace_id, log.trace_id)
            self.assertEqual(span.status.status_code, trace.StatusCode.ERROR)
        self.assertTrue(any('actual exception detail' in log.body for log in logs))
        self.assertTrue(any('value=7' in log.body for log in logs))

    def test_fixture_error_without_inventing_test_execution(self):
        class BrokenFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError('fixture failed')
            def test_not_run(self):
                pass
        result, spans, logs, _ = self.run_instrumented(BrokenFixture)
        self.assertEqual(result.testsRun, 0)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(spans), 2)
        self.assertEqual(len(logs), 1)
        self.assertIn('fixture failed', logs[0].body)

    def test_sdk_import_with_python_environment_disabled(self):
        import opentelemetry.sdk.trace
        sdk_dir = str(Path(opentelemetry.sdk.trace.__file__).parents[3])
        runner_path = str(Path(__file__).resolve().parents[2] / 'src/Mod/Test/TelemetryTestRunner.py')
        code = """
import importlib.util
import os
import runpy
import sys
assert importlib.util.find_spec('opentelemetry') is None
os.environ['FREECAD_TEST_OTEL'] = 'true'
os.environ['FREECAD_OTEL_PYTHONPATH'] = sys.argv[2]
module = runpy.run_path(sys.argv[1])
module['text_test_runner']()
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
"""
        result = subprocess.run([sys.executable, '-I', '-S', '-c', code, runner_path, sdk_dir],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_disabled_uses_standard_runner(self):
        with patch.dict(os.environ, {'FREECAD_TEST_OTEL': ''}):
            self.assertIs(type(text_test_runner()), unittest.TextTestRunner)


if __name__ == '__main__':
    unittest.main()
