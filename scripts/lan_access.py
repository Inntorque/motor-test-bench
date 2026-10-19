"""Local-network access gate; the hardware bridge remains loopback-only."""
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from http.cookies import SimpleCookie, CookieError
from pathlib import Path


def credentials(root):
    path = Path(root) / '.runtime' / 'lan-access.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as file:
            path.chmod(0o600)
            json.dump({'password': secrets.token_urlsafe(12), 'secret': secrets.token_hex(32)}, file)
    except FileExistsError:
        pass
    data = json.loads(path.read_text(encoding='utf-8'))
    if len(data.get('password', '')) < 12 or len(data.get('secret', '')) < 32:
        raise ValueError('局域网访问口令文件无效，请检查 .runtime/lan-access.json')
    return data


def lan_urls(port=5173):
    addresses = set()
    try:
        addresses.update(row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return [f'http://{address}:{port}/' for address in sorted(addresses)
            if ipaddress.ip_address(address).is_private and not ipaddress.ip_address(address).is_loopback]


class Access:
    def __init__(self, data):
        self.password = data['password']
        self.secret = data['secret'].encode()
        self.failures = {}
        self.lock = threading.Lock()

    def _sign(self, value):
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def login(self, password, address):
        now = time.time()
        with self.lock:
            self.failures = {key: entry for key, entry in self.failures.items() if now - entry[1] < 60}
            count, began = self.failures.get(address, (0, now))
            if count >= 10:
                return None
            if not hmac.compare_digest(password.encode(), self.password.encode()):
                self.failures[address] = (count + 1, began)
                return None
            self.failures.pop(address, None)
        value = f'{int(now + 12 * 3600)}.{secrets.token_hex(12)}'
        return value + '.' + self._sign(value)

    def valid(self, header):
        try:
            cookie = SimpleCookie(header or '')
            expires, nonce, signature = cookie['workbench_session'].value.split('.')
            return (time.time() < int(expires) <= time.time() + 12 * 3600 + 5
                    and hmac.compare_digest(signature, self._sign(expires + '.' + nonce)))
        except (KeyError, ValueError, TypeError, CookieError):
            return False


LOGIN = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>扭矩工作台 · 局域网登录</title>
<style>body{font:16px system-ui;background:#f3f6fa;color:#203047;margin:12vh auto;padding:24px;max-width:440px}
form{background:white;padding:28px;border-radius:12px}input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:14px;font:inherit}button{background:#203047;color:white;border:0;border-radius:6px}p{line-height:1.7}</style>
<form method="post" action="/login"><h2>扭矩工作台</h2><p>请输入安装电脑生成的局域网访问口令。</p>
<input name="password" type="password" autocomplete="current-password" required autofocus aria-label="访问口令">
<button>进入工作台</button><p>访问口令见安装电脑的“局域网访问说明.txt”。</p></form></html>'''
