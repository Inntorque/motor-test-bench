"""Launcher ownership and process locks; no serial or CAN devices are opened."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from scripts.instance_lock import InstanceBusy, InstanceLock
from scripts import workbench


class InstanceLockTests(unittest.TestCase):
    def test_competing_process_is_rejected_and_released_lock_can_be_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'instance.lock'
            code = ('import sys; from scripts.instance_lock import InstanceLock, InstanceBusy; '
                    'lock=InstanceLock(sys.argv[1]);\n'
                    'try: lock.__enter__()\n'
                    'except InstanceBusy: sys.exit(7)\n'
                    'lock.__exit__()')
            with InstanceLock(path):
                result = subprocess.run([sys.executable, '-c', code, str(path)],
                                        cwd=workbench.ROOT, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 7, result.stderr.decode())
            result = subprocess.run([sys.executable, '-c', code, str(path)],
                                    cwd=workbench.ROOT, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_crashed_process_does_not_leave_a_stale_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'instance.lock'
            code = ('import os,sys; from scripts.instance_lock import InstanceLock; '
                    'lock=InstanceLock(sys.argv[1]);lock.__enter__();os._exit(9)')
            result = subprocess.run([sys.executable, '-c', code, str(path)],
                                    cwd=workbench.ROOT, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 9)
            with InstanceLock(path):
                self.assertTrue(path.exists())

    def test_second_service_is_rejected_before_binding_or_device_start(self):
        from workbench import service

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with InstanceLock(root / '.runtime/application.lock'):
                with patch.object(service, 'ROOT', root), patch.object(service, '_serve') as serve:
                    with self.assertRaises(InstanceBusy):
                        service.serve(port=0)
                    serve.assert_not_called()


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.identity = {'app': 'motor-bench-web', 'hostApi': 1, 'directory': str(workbench.ROOT),
                         'pid': os.getpid(), 'lan': False}
        owner = self

        class Status(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.assertEqual(self.path, '/__workbench/status')
                body = json.dumps(owner.identity).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Status)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_repeat_start_reuses_server_without_overwriting_process_owner(self):
        entries = {'web': {'pid': 123, 'identity': 'owned.py'}}
        with patch.object(workbench.subprocess, 'Popen') as spawn, patch.object(workbench, 'save') as save:
            workbench.spawn('web', [], self.port, entries)
        spawn.assert_not_called()
        save.assert_not_called()
        self.assertEqual(entries['web']['pid'], 123)

    def test_occupied_port_never_starts_or_kills_a_process(self):
        self.identity['app'] = 'other-application'
        with patch.object(workbench.subprocess, 'Popen') as spawn, patch.object(workbench.os, 'killpg', create=True) as kill:
            with self.assertRaisesRegex(RuntimeError, '占用'):
                workbench.spawn('web', [], self.port, {})
        spawn.assert_not_called()
        kill.assert_not_called()

    def test_version_directory_lan_and_spawned_pid_are_checked(self):
        original = dict(self.identity)
        for field, value in (('hostApi', 0), ('directory', '/different-project'), ('lan', True), ('pid', -1)):
            with self.subTest(field=field):
                self.identity = {**original, field: value}
                with self.assertRaises(RuntimeError):
                    workbench.web_status(self.port, lan=False, pid=os.getpid())

    def test_existing_application_opens_without_rechecking_dependencies_or_starting_legacy_services(self):
        with tempfile.TemporaryDirectory() as folder:
            with (patch.object(workbench, 'RUNTIME', Path(folder)),
                  patch.object(workbench, 'state', return_value={}),
                  patch.object(workbench, 'local_config', return_value={}),
                  patch.object(workbench, 'listening', return_value=True),
                  patch.object(workbench, 'web_status', return_value=self.identity),
                  patch.object(workbench, 'migrate_config') as migrate,
                  patch.object(workbench.subprocess, 'run') as run,
                  patch.object(workbench.subprocess, 'Popen') as spawn,
                  patch.object(workbench.webbrowser, 'open') as browser,
                  patch.dict(os.environ, {'MOTORBENCH_NO_BROWSER': ''})):
                workbench.start()
        migrate.assert_not_called()
        run.assert_not_called()
        spawn.assert_not_called()
        browser.assert_called_once_with('http://127.0.0.1:5173/')
