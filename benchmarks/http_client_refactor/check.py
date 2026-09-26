"""Offline HTTP contract tests; loaded candidate code runs only inside the sandbox."""
import ast
import contextlib
import importlib.util
import inspect
import io
import json
import logging
from pathlib import Path
import platform
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch


class RequestException(Exception):
    def __init__(self, message='transport-secret', response=None):
        super().__init__(message)
        self.response = response


class ConnectionError(RequestException):
    pass


class Timeout(RequestException):
    pass


class HTTPError(RequestException):
    pass


class Response:
    def __init__(self, status=200, value=None, json_error=None):
        self.status_code = status
        self.value = {'ok': True} if value is None else value
        self.json_error = json_error
        self.json_calls = 0
        self.checked = False
        self.error = HTTPError(response=self)

    def __bool__(self):
        return self.status_code < 400

    def raise_for_status(self):
        self.checked = True
        if self.status_code >= 400:
            raise self.error

    def json(self):
        assert self.checked, 'raise_for_status must precede JSON decoding'
        self.json_calls += 1
        if self.json_error:
            raise self.json_error
        return self.value


def requests_double():
    requests = types.ModuleType('requests')
    exceptions = types.ModuleType('requests.exceptions')
    for cls in (RequestException, ConnectionError, Timeout, HTTPError):
        setattr(requests, cls.__name__, cls)
        setattr(exceptions, cls.__name__, cls)
    requests.exceptions = exceptions
    requests.request = Mock()
    requests.get = lambda url, **kwargs: requests.request('GET', url, **kwargs)
    requests.post = lambda url, **kwargs: requests.request('POST', url, **kwargs)
    return requests


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Contract(unittest.TestCase):
    project = Path.cwd()
    oracle = Path(__file__).resolve().parent / 'oracle'

    def setUp(self):
        self.requests = requests_double()
        self.modules = patch.dict(sys.modules, {'requests': self.requests,
                                                'requests.exceptions': self.requests.exceptions})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.sleep_patch = patch('time.sleep')
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.client = load(self.project / 'src/http_client.py', 'candidate_client')
        self.baseline = load(self.oracle / 'src/http_client.py', 'baseline_client')
        self.assertTrue(callable(getattr(self.client, 'request_json', None)), 'missing request_json')
        self.assertTrue(issubclass(self.client.RequestError, RuntimeError))
        self.assertEqual(self.client.DEFAULT_TIMEOUT, 10)

    def test_public_signatures_and_shared_dispatch(self):
        signature = inspect.signature(self.client.request_json)
        self.assertEqual(str(signature), '(method, url, **kwargs)')
        self.assertEqual(self.client.BASE_URL, self.baseline.BASE_URL)
        for name in ('fetch_user', 'fetch_order', 'post_event'):
            self.assertEqual(inspect.signature(getattr(self.client, name)),
                             inspect.signature(getattr(self.baseline, name)))
            sentinel = object()
            with patch.object(self.client, 'request_json', return_value=sentinel) as helper:
                self.assertIs(getattr(self.client, name)('id'), sentinel)
                helper.assert_called_once()

    def test_wrappers_preserve_success_behavior(self):
        for name, argument in [('fetch_user', 'a/b'), ('fetch_order', 7),
                               ('post_event', {'message': '世界'})]:
            for value in ({'id': 4}, ['one', 'two'], False):
                with self.subTest(name=name, value=value):
                    self.requests.request.reset_mock()
                    self.requests.request.return_value = Response(value=value)
                    expected = getattr(self.baseline, name)(argument)
                    before = self.requests.request.call_args
                    self.requests.request.reset_mock()
                    self.requests.request.return_value = Response(value=value)
                    actual = getattr(self.client, name)(argument)
                    self.assertEqual(actual, expected)
                    self.assertEqual(self.requests.request.call_args, before)
                    self.requests.request.assert_called_once()
        self.sleep.assert_not_called()

    def test_default_timeout_is_read_at_call_time(self):
        self.client.DEFAULT_TIMEOUT = 17
        self.requests.request.return_value = Response()
        self.client.request_json('GET', 'url')
        self.assertEqual(self.requests.request.call_args.kwargs['timeout'], 17)
        for name, expected in [('fetch_user', 5), ('fetch_order', 17), ('post_event', 17)]:
            getattr(self.client, name)('argument')
            self.assertEqual(self.requests.request.call_args.kwargs['timeout'], expected)
        for timeout in (None, 2, (1, 3)):
            self.client.request_json('GET', 'url', timeout=timeout, headers={'X-Test': 'ok'}, params={'p': 1})
            self.assertEqual(self.requests.request.call_args.kwargs,
                             {'timeout': timeout, 'headers': {'X-Test': 'ok'}, 'params': {'p': 1}})

    def test_transient_get_retries_then_succeeds(self):
        for first in (ConnectionError(), Timeout(), Response(500), Response(503), Response(599)):
            for method in ('GET', 'get'):
                with self.subTest(error=type(first).__name__, method=method):
                    self.requests.request.reset_mock()
                    self.sleep.reset_mock()
                    self.requests.request.side_effect = [first, Timeout(), Response(value={'done': True})]
                    with self.assertLogs(level='WARNING') as logged:
                        result = self.client.request_json(method, 'url', headers={'X': 'y'})
                    self.assertEqual(result, {'done': True})
                    self.assertEqual(self.requests.request.call_count, 3)
                    self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.1, 0.2])
                    self.assertEqual([r.levelno for r in logged.records], [logging.WARNING] * 2)
                    self.assertTrue(all(c == self.requests.request.call_args_list[0]
                                        for c in self.requests.request.call_args_list))

    def test_exhausted_get_stops_and_preserves_cause(self):
        for failure in (ConnectionError(), Timeout(), Response(503)):
            with self.subTest(error=type(failure).__name__):
                self.requests.request.reset_mock()
                self.sleep.reset_mock()
                self.requests.request.side_effect = [failure, failure, failure]
                with self.assertLogs(level='WARNING') as logged:
                    with self.assertRaises(self.client.RequestError) as caught:
                        self.client.request_json('GET', 'url')
                cause = failure.error if isinstance(failure, Response) else failure
                self.assertIs(caught.exception.__cause__, cause)
                self.assertEqual(self.requests.request.call_count, 3)
                self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.1, 0.2])
                self.assertEqual([r.levelno for r in logged.records],
                                 [logging.WARNING, logging.WARNING, logging.ERROR])

    def assert_terminal(self, method, failure):
        self.requests.request.reset_mock()
        self.sleep.reset_mock()
        self.requests.request.side_effect = [failure]
        with self.assertLogs(level='ERROR') as logged:
            with self.assertRaises(self.client.RequestError) as caught:
                self.client.request_json(method, 'url')
        cause = failure.error if isinstance(failure, Response) else failure
        if isinstance(failure, Response) and failure.json_error:
            cause = failure.json_error
        self.assertIs(caught.exception.__cause__, cause)
        self.requests.request.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual([r.levelno for r in logged.records], [logging.ERROR])

    def test_post_and_other_methods_never_retry(self):
        for method in ('POST', 'post', 'PUT', 'DELETE'):
            for failure in (ConnectionError(), Timeout(), Response(503)):
                with self.subTest(method=method, failure=type(failure).__name__):
                    self.assert_terminal(method, failure)

    def test_4xx_and_other_request_errors_do_not_retry(self):
        for failure in (Response(400), Response(404), Response(429), Response(600), RequestException()):
            with self.subTest(failure=type(failure).__name__):
                self.assert_terminal('GET', failure)
                if isinstance(failure, Response):
                    self.assertEqual(failure.json_calls, 0)

    def test_invalid_json_does_not_retry(self):
        self.assert_terminal('GET', Response(json_error=ValueError('bad-json-secret')))

    def test_programming_errors_are_not_wrapped(self):
        failure = TypeError('programming error')
        self.requests.request.side_effect = failure
        with self.assertRaises(TypeError) as caught:
            self.client.request_json('GET', 'url')
        self.assertIs(caught.exception, failure)
        self.sleep.assert_not_called()

    def test_logs_do_not_expose_request_data_or_print(self):
        output = io.StringIO()
        self.requests.request.side_effect = [Timeout('exception-secret')] * 3
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertLogs(level='WARNING') as logged:
                with self.assertRaises(self.client.RequestError):
                    self.client.request_json('GET', 'https://url-secret/path',
                        headers={'Authorization': 'credential-secret'}, json={'value': 'payload-secret'})
        self.assertEqual(output.getvalue(), '')
        for secret in ('exception-secret', 'url-secret', 'credential-secret', 'payload-secret'):
            self.assertNotIn(secret, '\n'.join(logged.output))

    def test_imports_remain_stdlib_plus_requests(self):
        tree = ast.parse((self.project / 'src/http_client.py').read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, 'No added local dependencies')
                names = [node.module.split('.')[0]] if node.module else []
            for name in names:
                self.assertIn(name, sys.stdlib_module_names | {'requests'})


def evaluate(project, oracle):
    class CandidateContract(Contract):
        pass
    CandidateContract.project, CandidateContract.oracle = Path(project), Path(oracle)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CandidateContract)
    result = unittest.TestResult()
    suite.run(result)
    return {'passed': result.wasSuccessful(), 'cases': result.testsRun,
            'errors': [f'{test.id()}: {error}' for test, error in result.failures + result.errors]}


def main():
    started = time.monotonic()
    result = evaluate(Path.cwd(), Path(__file__).resolve().parent / 'oracle')
    result.update(check_seconds=time.monotonic() - started, python=platform.python_version())
    print('BENCH_RESULT=' + json.dumps(result))
    return int(not result['passed'])


if __name__ == '__main__':
    sys.exit(main())
