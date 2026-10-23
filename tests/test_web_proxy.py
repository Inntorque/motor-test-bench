import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from scripts.web_server import Handler


class Echo(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        data=json.dumps(dict(client=self.headers.get('X-Workbench-Client'),
            epoch=self.headers.get('X-Control-Epoch'),token=self.headers.get('X-MotorBench-Token'))).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


class ProxyTests(unittest.TestCase):
    def test_production_proxy_preserves_control_ownership_and_epoch_headers(self):
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Echo)
        proxy=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (upstream,proxy)]
        for thread in threads:thread.start()
        try:
            with patch.object(Handler,'bridge_port',upstream.server_port):
                connection=http.client.HTTPConnection('127.0.0.1',proxy.server_port,timeout=2)
                connection.request('POST','/api/echo','{}',{'X-Workbench-Client':'a'*32,
                    'X-Control-Epoch':'7','X-MotorBench-Token':'test-token'})
                response=connection.getresponse();data=json.loads(response.read());connection.close()
                self.assertEqual(response.status,200)
                self.assertEqual(data,dict(client='a'*32,epoch='7',token='test-token'))
        finally:
            for server in (proxy,upstream):server.shutdown();server.server_close()
            for thread in threads:thread.join(1)


if __name__=='__main__':unittest.main()
