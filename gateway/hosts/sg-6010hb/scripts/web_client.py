"""Local dashboard HTTP client. Writes use authenticated POST, never retry.

Existing commissioning scripts can keep calling urlopen(url, timeout=...).
A server restart or another client's STOP invalidates their command epoch;
restart the commissioning script deliberately instead of auto-resending.
"""
import json
import threading
import urllib.request
from urllib.parse import urlsplit

MUTATING_ROUTES = frozenset({
    '/api/motor', '/api/cur', '/api/phase', '/api/brake', '/api/control/set',
    '/api/control/calibrate', '/api/set', '/api/save', '/api/load',
    '/api/telem', '/api/reconnect', '/api/motor_profile', '/api/ota/onekey',
    '/api/diag/arm', '/api/diag/off', '/api/diag/dump', '/api/shutdown',
})
_sessions = {}
_lock = threading.Lock()

def urlopen(url, timeout=8):
    parsed = urlsplit(url)
    if parsed.path.rstrip('/') not in MUTATING_ROUTES:
        return urllib.request.urlopen(url, timeout=timeout)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Motor control client only supports the local dashboard')
    origin = '%s://%s' % (parsed.scheme, parsed.netloc)
    with _lock:
        if origin not in _sessions:
            with urllib.request.urlopen(origin + '/api/session', timeout=timeout) as r:
                _sessions[origin] = json.load(r)
        session = dict(_sessions[origin])
    req = urllib.request.Request(url, data=b'', method='POST', headers={
        'X-Control-Token': session['token'],
        'X-Command-Epoch': str(session['command_epoch']),
    })
    response = urllib.request.urlopen(req, timeout=timeout)
    epoch = response.headers.get('X-Command-Epoch')
    if epoch is not None:
        with _lock:
            _sessions[origin]['command_epoch'] = max(
                _sessions[origin]['command_epoch'], int(epoch))
    return response
