# SPDX-License-Identifier: LGPL-2.1-or-later
"""Opt-in direct OpenTelemetry instrumentation for FreeCAD unittest commands."""
import logging
import os
import unittest


def text_test_runner(**kwargs):
    # Normal FreeCAD installs do not require OpenTelemetry packages.
    if os.getenv('FREECAD_TEST_OTEL') != 'true':
        return unittest.TextTestRunner(**kwargs)
    return TelemetryRunner(**kwargs)


class TelemetryRunner(unittest.TextTestRunner):
    def run(self, test):
        from opentelemetry import trace
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        resource = Resource.create({
            'service.name': 'freecad-tests',
            'github.repository': os.getenv('GITHUB_REPOSITORY', ''),
            'github.run.id': os.getenv('GITHUB_RUN_ID', ''),
            'github.run.attempt': os.getenv('GITHUB_RUN_ATTEMPT', ''),
            'telemetry.source': 'unittest-direct',
        })
        endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://127.0.0.1:4318').rstrip('/')
        traces = TracerProvider(resource=resource)
        logs = LoggerProvider(resource=resource)
        traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + '/v1/traces', timeout=5)))
        logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint + '/v1/logs', timeout=5)))
        tracer = traces.get_tracer('freecad.unittest')
        logger = logging.Logger('freecad.unittest', level=logging.ERROR)
        handler = LoggingHandler(logger_provider=logs)
        logger.addHandler(handler)
        group = os.getenv('FREECAD_TEST_GROUP', 'Python tests')
        context = TraceContextTextMapPropagator().extract({'traceparent': os.getenv('TRACEPARENT', '')})
        previous = self.resultclass
        try:
            with tracer.start_as_current_span(group, context=context) as parent:
                def result_factory(*args, **kwargs):
                    return TelemetryResult(*args, tracer=tracer, logger=logger, group=group, **kwargs)
                self.resultclass = result_factory
                result = super().run(test)
                if not result.wasSuccessful():
                    parent.set_status(trace.Status(trace.StatusCode.ERROR, 'Python tests failed'))
                return result
        finally:
            self.resultclass = previous
            # FreeCAD exits after the command: drain both SDK queues before returning.
            try:
                traces.force_flush(timeout_millis=10000)
                logs.force_flush(timeout_millis=10000)
            finally:
                traces.shutdown()
                logs.shutdown()
                handler.close()


class TelemetryResult(unittest.TextTestResult):
    def __init__(self, *args, tracer, logger, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracer, self.logger, self.group = tracer, logger, group
        self.active = {}

    def _start(self, test):
        from opentelemetry import trace
        span = self.tracer.start_span(test.id(), attributes={
            'test.name': test.id(), 'test.framework': 'unittest', 'test.group': self.group,
        })
        scope = trace.use_span(span, end_on_exit=True)
        scope.__enter__()
        self.active[id(test)] = (span, scope)

    def startTest(self, test):
        super().startTest(test)
        self._start(test)

    def _failure(self, test, message):
        from opentelemetry import trace
        fixture = id(test) not in self.active
        if fixture:
            # Fixture errors have no startTest callback; this is a diagnostic span.
            self._start(test)
        span, _ = self.active[id(test)]
        span.set_attribute('test.status', 'failure')
        span.set_status(trace.Status(trace.StatusCode.ERROR, 'Python test failed'))
        self.logger.error(message, extra={'test.name': test.id(), 'test.group': self.group})
        if fixture:
            self._end(test)

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self._failure(test, self._exc_info_to_string(error, test))

    def addError(self, test, error):
        super().addError(test, error)
        self._failure(test, self._exc_info_to_string(error, test))

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            self._failure(test, f'{subtest.id()}\n{self._exc_info_to_string(error, subtest)}')

    def addSuccess(self, test):
        super().addSuccess(test)
        self.active[id(test)][0].set_attribute('test.status', 'success')

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if id(test) in self.active:
            self.active[id(test)][0].set_attribute('test.status', 'skipped')

    def addExpectedFailure(self, test, error):
        super().addExpectedFailure(test, error)
        self.active[id(test)][0].set_attribute('test.status', 'expected_failure')

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._failure(test, 'Unexpected success of a test marked expectedFailure')

    def _end(self, test):
        _, scope = self.active.pop(id(test))
        scope.__exit__(None, None, None)

    def stopTest(self, test):
        self._end(test)
        super().stopTest(test)
