"""Named test objects and mechanical setups. Never writes device parameters."""
import copy
import json
import math
from pathlib import Path
import threading
import uuid

SETUP_COLUMNS = ['test_setup_id', 'test_setup_name', 'gearbox_name', 'has_gearbox',
                 'reduction_ratio', 'measurement_axis', 'measured_object_mass_kg', 'measured_object_volume_L']


def setup_columns(subject):
    setup = (subject or {}).get('testSetup') or {}
    return dict(zip(SETUP_COLUMNS, [setup.get(key) for key in
                ('id', 'name', 'gearboxName', 'hasGearbox', 'ratio', 'axis', 'massKg', 'volumeL')]))


def setup_parameters(subject):
    setup = (subject or {}).get('testSetup')
    if not setup:
        raise ValueError('请先为所选电机选择裸机或带减速箱测试配置')
    return {key: setup.get(key) for key in ('ratio', 'axis', 'massKg', 'volumeL')} | {
        'directShaftConfirmed': setup['axis'] == 'motor'}


class TestSubjects:
    GROUPS = {'motor': 'motors', 'driverBoard': 'driverBoards'}

    def __init__(self, path, legacy_profile=None, busy=None):
        self.path = Path(path)
        self.legacy_profile = legacy_profile
        self.busy = busy
        self.lock = threading.RLock()
        self.cached = None
        self.file_stamp = None

    @staticmethod
    def _stamp(path):
        try:
            stat = path.stat()
            return stat.st_ino, stat.st_size, stat.st_mtime_ns
        except FileNotFoundError:
            return None

    def _read(self):
        stamp = self._stamp(self.path)
        if self.cached is not None and stamp == self.file_stamp:
            return self.cached
        data = json.loads(self.path.read_text(encoding='utf-8')) if stamp is not None else {
            'motors': [], 'driverBoards': [], 'selected': {'motorId': '', 'driverBoardId': ''}}
        if data.get('schema', 1) < 2:
            data.update(schema=2, testSetups=[])
            data['selected']['testSetupId'] = ''
            legacy = (self.legacy_profile or {}).get('motor') or {}
            ratio = (legacy.get('nameplate') or {}).get('reduction_ratio')
            if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and math.isfinite(ratio) and ratio > 0:
                name = legacy.get('label') or '原设备'
                motor = next((row for row in data['motors'] if row['name'].casefold() == name.casefold()), None)
                if motor is None:
                    motor = {'id': uuid.uuid4().hex, 'name': name}
                    data['motors'].append(motor)
                data['testSetups'].append({'id': uuid.uuid4().hex, 'motorId': motor['id'],
                    'name': '原带减速箱配置（待确认）', 'hasGearbox': True, 'gearboxName': '',
                    'ratio': ratio, 'axis': 'output', 'massKg': None, 'volumeL': None,
                    'revision': 0,
                    'ratioConfirmed': False, 'ratioSource': '旧设备档案；待现场核对'})
            self._save(data)
            return self.cached
        self.cached, self.file_stamp = data, stamp
        return self.cached

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        stamp = self._stamp(temporary)
        temporary.replace(self.path)
        self.cached, self.file_stamp = copy.deepcopy(data), stamp

    def _editable(self):
        if self.busy and self.busy():
            raise ValueError('测试或记录进行中，请结束后再切换或修改测试配置')

    def status(self):
        with self.lock:
            return {**copy.deepcopy(self._read()), 'configurationLocked': bool(self.busy and self.busy())}

    def create(self, kind, name):
        if not isinstance(kind, str) or kind not in self.GROUPS or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('请选择电机或驱动板，并填写 1–80 字的名称')
        name = name.strip()
        with self.lock:
            data = copy.deepcopy(self._read())
            items = data[self.GROUPS[kind]]
            item = next((row for row in items if row['name'].casefold() == name.casefold()), None)
            if item is None:
                item = {'id': uuid.uuid4().hex, 'name': name}
                items.append(item)
                self._save(data)
            return {'catalog': data, 'item': item}

    def save_setup(self, value):
        if not isinstance(value, dict): raise ValueError('测试配置必须为对象')
        name = value.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('测试配置名称需为 1–80 字')
        gearbox = value.get('hasGearbox')
        if not isinstance(gearbox, bool): raise ValueError('请选择裸机或带减速箱')
        ratio = value.get('ratio')
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not math.isfinite(ratio) or ratio < .000001:
            raise ValueError('减速比必须为大于零的有效数值')
        if not gearbox and ratio != 1: raise ValueError('裸机直测的转子/被测轴传动比必须为 1')
        gearbox_name = value.get('gearboxName', '')
        if not isinstance(gearbox_name, str) or len(gearbox_name) > 80: raise ValueError('减速箱名称最长 80 字')
        sizes = {}
        for key in ('massKg', 'volumeL'):
            number = value.get(key)
            if number is not None and (not isinstance(number, (int, float)) or isinstance(number, bool) or not math.isfinite(number) or number <= 0):
                raise ValueError('被测对象质量和体积需为正数，未知时留空')
            sizes[key] = number
        with self.lock:
            self._editable()
            data = copy.deepcopy(self._read())
            motor_id = value.get('motorId')
            if not any(row['id'] == motor_id for row in data['motors']): raise ValueError('请先选择电机')
            identifier = value.get('id', '')
            if not isinstance(identifier, str): raise ValueError('测试配置编号无效')
            existing = next((row for row in data['testSetups'] if row['id'] == identifier and row['motorId'] == motor_id), None)
            if identifier and existing is None: raise ValueError('测试配置不存在或不属于所选电机')
            if any(row['motorId'] == motor_id and row['name'].casefold() == name.strip().casefold() and row['id'] != identifier for row in data['testSetups']):
                raise ValueError('该电机下已有同名测试配置')
            item = {'id': identifier or uuid.uuid4().hex, 'motorId': motor_id, 'name': name.strip(),
                    'hasGearbox': gearbox, 'gearboxName': gearbox_name.strip() if gearbox else '',
                    'ratio': ratio, 'axis': 'output' if gearbox else 'motor', **sizes,
                    'revision': existing.get('revision', 0)+1 if existing else 1,
                    'ratioConfirmed': True, 'ratioSource': '操作者保存的测试配置'}
            if existing is None: data['testSetups'].append(item)
            else: data['testSetups'][data['testSetups'].index(existing)] = item
            self._save(data)
            return {'catalog': data, 'item': item}

    def select(self, selected):
        with self.lock:
            self._editable()
            data = copy.deepcopy(self._read())
            result = {}
            for kind, group in self.GROUPS.items():
                key = kind+'Id'
                identifier = selected.get(key, '')
                if not isinstance(identifier, str): raise ValueError('选择无效')
                if identifier and not any(row['id'] == identifier for row in data[group]):
                    raise ValueError('选择的电机或驱动板不存在，请重新选择')
                result[key] = identifier
            identifier = selected.get('testSetupId', '')
            if not isinstance(identifier, str): raise ValueError('测试配置选择无效')
            if identifier and not any(row['id'] == identifier and row['motorId'] == result['motorId'] for row in data['testSetups']):
                raise ValueError('测试配置不属于所选电机，请重新选择')
            result['testSetupId'] = identifier
            data['selected'] = result
            self._save(data)
            return data

    def selection(self):
        with self.lock:
            data = self._read()
            result = {kind: next((row for row in data[group]
                                 if row['id'] == data['selected'].get(kind+'Id')), None)
                      for kind, group in self.GROUPS.items()}
            setup = next((row for row in data['testSetups'] if row['id'] == data['selected'].get('testSetupId') and row['motorId'] == data['selected'].get('motorId')), None)
            if setup: result['testSetup'] = setup
            return copy.deepcopy(result)
