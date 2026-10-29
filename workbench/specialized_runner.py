"""Specialized-only recipes using the existing motor adapter and control lease."""
import json
import copy
import threading
import time
import uuid
from protocol import generated as p
from .specialized_capture import Capture
from .specialized_metrics import finite, positioning
from .recording import RecordingView
from .subjects import setup_parameters


def config(data):
    if not isinstance(data, dict): raise ValueError('专项配方必须为对象')
    category = data.get('suite')
    if category not in ('performance', 'position', 'thermal'): raise ValueError('未知专项项目')
    def number(key, default, low=0, high=None):
        value = data.get(key, default)
        if not finite(value) or value < low or high is not None and value > high:
            raise ValueError(f'{key} 数值无效')
        return value
    result = {'suite': category, 'name': str(data.get('name') or {'performance':'性能测试', 'position':'定位精度', 'thermal':'温升曲线'}[category])[:100],
              'ratio': number('ratio', 1, .000001), 'axis': data.get('axis', 'output'),
              'durationS': number('durationS', 60, .1), 'windowS': number('windowS', 1, .1, 60),
              'settleS': number('settleS', 1, 0), 'dwellS': number('dwellS', 2, .2),
              'coolingS': number('coolingS', 0), 'thermalWindowS': number('thermalWindowS', 600, 1),
              'thermalSlopeCPerMin': number('thermalSlopeCPerMin', .1),
              'stopWhenStable': data.get('stopWhenStable') is True,
              'bandwidthVerified': data.get('bandwidthVerified') is True,
              'ktMaxSpeedRpm': number('ktMaxSpeedRpm', 5), 'ktMaxIdA': number('ktMaxIdA', .5),
              'controlMode': data.get('controlMode', 'IQ'), 'target': number('target', 1, -1e9, 1e9),
              'test': data.get('test', 'kt' if category == 'performance' else category),
              'motionConfirmed': data.get('motionConfirmed') is True,'directShaftConfirmed':data.get('directShaftConfirmed') is True,
              'sensorInfo': str(data.get('sensorInfo') or '')[:200],
              'coolingInfo': str(data.get('coolingInfo') or '')[:200]}
    for key in ('massKg', 'volumeL', 'ratedSpeedRpm', 'ratedTorqueNm', 'ambientC'):
        value = data.get(key)
        if value is not None and (not finite(value) or key in ('massKg','volumeL') and value <= 0):
            raise ValueError(f'{key} 数值无效，未知时留空')
        result[key] = value
    if result['axis'] not in ('output', 'motor'): raise ValueError('请选择测量轴端')
    if result['axis'] == 'motor' and (result['ratio'] != 1 or data.get('directShaftConfirmed') is not True):
        raise ValueError('电机本体直测需传感器直接接电机轴，减速比设为 1 并确认')
    if result['controlMode'] not in ('IQ', 'VELOCITY'): raise ValueError('未知工况控制方式')
    if category == 'position':
        points = data.get('positionsDeg', [0,45,90,135,180,225,270,315])
        if not isinstance(points, list) or not 2 <= len(points) <= 100 or any(not finite(v) for v in points):
            raise ValueError('定位需 2–100 个有限数值目标点')
        if len(set(points)) != len(points): raise ValueError('定位目标点不能重复')
        cycles = number('cycles', 5, 1, 100)
        if cycles != int(cycles): raise ValueError('重复次数须为整数')
        result.update(positionsDeg=sorted(points), cycles=int(cycles), approachDeg=number('approachDeg', 2, .001),
                      positionReference=data.get('positionReference', 'relative'))
        if result['positionReference'] not in ('relative','absolute'): raise ValueError('坐标方式无效')
    if category == 'performance':
        if result['test'] not in ('kt','point','capture'): raise ValueError('未知性能配方')
        levels = data.get('iqLevelsA', [.5,1,1.5,2])
        if not isinstance(levels, list) or not 3 <= len(levels) <= 40 or any(not finite(v) for v in levels):
            raise ValueError('Kt 需 3–40 个有效电流级')
        result['iqLevelsA'] = levels
    return result


def fresh(channel):
    stamp = (channel.get('snapshot') or {}).get('receivedAt')
    return channel.get('connected') and finite(stamp) and 0 <= time.time()-stamp <= 1.5


def selected_recipe(manager, data):
    subject = copy.deepcopy(manager.subjects() if manager.subjects else {})
    if manager.subjects:
        parameters = setup_parameters(subject)
        if data.get('testSetupId') is not None and data['testSetupId'] != subject['testSetup']['id']:
            raise ValueError('测试配置已变化，请刷新后重新开始')
        if data.get('testSetupRevision') is not None and data['testSetupRevision'] != subject['testSetup'].get('revision', 0):
            raise ValueError('测试配置已修改，请刷新后重新开始')
        data = {**data, **parameters}
    return config(data), subject


def public_report(report):
    value={**report}
    for key in ('preview','thermalPreview'):
        points=report.get(key,[])
        value[key]=points[::max(1,(len(points)+199)//200)] if points else []
    if 'positionVisits' in report:
        value['positionVisitCount']=len(report['positionVisits'])
        value['positionVisits']=report['positionVisits'][-20:]
    return value


def start_active(manager, data):
    recipe, subject = selected_recipe(manager, data)
    if not recipe['motionConfirmed']: raise ValueError('请确认本次电源、负载和机械运动条件')
    motor = manager.motor
    with motor.action_lock:
        if manager.active() or motor.run.get('running') or motor.thread and motor.thread.is_alive():
            raise ValueError('已有电机任务正在运行')
        if (manager.measurements.status() or {}).get('state') in ('running','settling'):
            raise ValueError('先结束当前专项采集')
        snapshot = motor._refresh(); motor._healthy(snapshot, starting=True)
        mode = 4 if recipe['suite'] == 'position' else 1 if recipe['test'] == 'kt' or recipe['controlMode'] == 'IQ' else 3
        if not snapshot.get('capabilities', {}).get('modes', 0) & (1 << mode):
            raise ValueError('驱动板未声明所需控制模式')
        limit = motor._limit()
        currents = recipe.get('iqLevelsA', []) if recipe['test'] == 'kt' else [recipe['target']] if mode == 1 else []
        if any(abs(v) > limit for v in currents): raise ValueError('目标电流超过设备声明的现有限值')
        speed_limit = snapshot.get('limits', {}).get(str(p.OBJ_ROTOR_SPEED_ABS_MAX))
        if mode == 3 and (not finite(speed_limit) or abs(recipe['target']*recipe['ratio']*1000) > speed_limit):
            raise ValueError('速度目标超过设备声明范围或设备未声明速度范围')
        source = manager.source()
        if recipe['suite'] == 'performance' and not fresh(source.get('sensor', {})):
            raise ValueError('性能测试需要新鲜转矩/转速测量')
        if recipe['suite'] == 'thermal' and not (snapshot.get('ntc') or {}).get('valid'):
            raise ValueError('温升测试需要有效温度源')
        position = snapshot.get('feedback', {}).get('position_deg')
        if recipe['suite'] == 'position' and (not finite(position) or snapshot['feedback'].get('position_stale')):
            raise ValueError('定位需要新鲜多圈位置反馈')
        origin = position/recipe['ratio'] if finite(position) else 0
        if recipe['suite']=='position':
            offset=origin if recipe['positionReference']=='relative' else 0
            bounds=[offset+min(recipe['positionsDeg'])-recipe['approachDeg'],offset+max(recipe['positionsDeg'])+recipe['approachDeg']]
            if any(not finite(value*recipe['ratio']/360*1000000) or not -(1<<63)<=round(value*recipe['ratio']/360*1000000)<(1<<63) for value in bounds):
                raise ValueError('定位目标及接近路径超出既有 64 位位置协议范围')
        report = {'id': uuid.uuid4().hex, 'specializedSchema': 2, 'category': recipe['suite'], 'name': recipe['name'],
                  'startedAt': time.time(), 'running': True, 'state': 'starting', 'phase': '准备', 'reason': '',
                  'config': recipe, 'originOutputDeg': origin, 'testSubject': subject,
                  'sourceAtStart': source, 'deviceAtStart': snapshot, 'positionVisits': [], 'stopConfirmed': None,
                  'referenceData': {}, 'protectionSeen': False}
        directory = manager.root/report['id']; directory.mkdir(parents=True)
        manager.measurements._save(directory/'report.json', report)
        motor.cancel = threading.Event()
        motor.run = {'phase':'automatic','running':True,'scriptId':report['id'],'targetA':None,'steps':0,'elapsed':0,'stopConfirmed':None,'acquisitionManaged':True}
        with manager.lock: manager.live = report
        manager.thread = threading.Thread(target=run_active, args=(manager,directory,report,mode), daemon=True)
        motor.thread = manager.thread
        try: manager.thread.start()
        except Exception:
            motor.run['running'] = False
            report.update(running=False,state='interrupted',reason='无法启动专项线程')
            manager.measurements._save(directory/'report.json',report)
            raise
        return manager.status()


def run_active(manager, directory, report, mode):
    motor = manager.motor; recipe = report['config']; capture = None; last_save = 0; sent_start = False; recording = None
    began = time.monotonic(); phase = '准备'; target = None
    last_publish = 0
    def publish(force=False):
        nonlocal last_save, last_publish
        if not force and time.monotonic()-last_publish < .25:return
        last_publish = time.monotonic()
        report.update(elapsedS=time.monotonic()-began, phase=phase)
        if capture: report.update(capture.status())
        if report['category'] == 'position': report['positionMetrics'] = positioning(report['positionVisits'],report['referenceData'])
        with manager.lock: manager.live = json.loads(json.dumps(public_report(report)))
        with motor.lock: motor.run.update(elapsed=report['elapsedS'],steps=len(report['positionVisits']))
        if force or time.monotonic()-last_save >= 2:
            manager.measurements._save(directory/'report.json',report); last_save=time.monotonic()
    def sample(cooling=False):
        if motor.cancel.is_set(): raise InterruptedError('操作者停止')
        if not cooling and motor.start_guard: motor.start_guard()
        snapshot = motor._refresh()
        if snapshot.get('motor', {}).get('fault'):
            report['protectionSeen'] = True
        if not cooling:motor._healthy(snapshot)
        state = snapshot.get('motor') or {}
        if not cooling and (state.get('mode') != mode or not state.get('output_on')):
            raise ValueError('设备意外停止或控制模式改变')
        if recipe['suite'] == 'thermal' and not snapshot.get('ntc', {}).get('valid'):
            raise ValueError('温度源失效')
        sources, row = capture.poll(phase,target)
        if recipe['suite'] == 'performance' and not cooling and not fresh(sources.get('sensor', {})):
            raise ValueError('转矩/转速测量失联')
        publish()
        return row
    def hold(seconds, cooling=False, collect=False):
        from specialized_tests import Stats
        end = time.monotonic()+seconds; positions=Stats();last_stamp=None;started=time.time()
        while time.monotonic() < end:
            row = sample(cooling)
            if collect and row and row['stamp']>=started and row['stamp']!=last_stamp:
                positions.add(row['position']);last_stamp=row['stamp']
            if recipe['stopWhenStable'] and phase == 'heating' and capture.status()['thermal'].get('stable'):
                break
            motor.cancel.wait(.05)
        return positions.result()
    def set_target(value):
        if motor.cancel.is_set(): raise InterruptedError('操作者停止')
        if motor.start_guard: motor.start_guard()
        oid, raw = (p.OBJ_POSITION_TARGET, round(value*recipe['ratio']/360*1000000)) if mode == 4 else (p.OBJ_IQ_TARGET,round(value*1000)) if mode == 1 else (p.OBJ_VELOCITY_TARGET,round(value*recipe['ratio']*1000))
        response = motor.device.set_object_target(oid,raw)
        capture.write('command',time.time(),{'object':oid,'value':raw,'response':response})
        if mode == 1:
            with motor.lock: motor.run['targetA'] = value
    try:
        if manager.recorder:
            recording=RecordingView(manager.recorder,recipe['name'],recipe['ratio'],test_subject=report['testSubject']);recording.start_recording()
            report['recordingId']=recording.identifier
        capture = Capture(directory,manager.source,manager.measurements.frames,recipe,report['testSubject'])
        initial = 0 if mode == 1 else report['originOutputDeg'] if mode == 4 else recipe['target']
        objects = {p.OBJ_RUN_MODE:mode,p.OBJ_DIRECTION:1,p.OBJ_IQ_TARGET:0,p.OBJ_PHASE_RUN_LIMIT:0}
        if mode == 4: objects[p.OBJ_POSITION_TARGET] = round(initial*recipe['ratio']/360*1000000)
        if mode == 3: objects[p.OBJ_VELOCITY_TARGET] = round(initial*recipe['ratio']*1000)
        if motor.start_guard: motor.start_guard()
        prepared = motor.device.prepare_run(objects)
        if motor.cancel.is_set(): raise InterruptedError('操作者停止')
        if motor.start_guard: motor.start_guard()
        sent_start = True
        motor.device.start(prepared)
        capture.write('start_ack',time.time(),{'preparedId':prepared,'mode':mode})
        if recipe['suite'] == 'position':
            origin = report['originOutputDeg'] if recipe['positionReference'] == 'relative' else 0
            for cycle in range(recipe['cycles']):
                for direction in ('forward','reverse'):
                    points = recipe['positionsDeg'] if direction == 'forward' else list(reversed(recipe['positionsDeg']))
                    phase = '接近目标'; target = origin+points[0]+(-recipe['approachDeg'] if direction=='forward' else recipe['approachDeg'])
                    set_target(target); hold(recipe['settleS'])
                    for nominal in points:
                        phase = '定位稳定'; target = origin+nominal
                        set_target(target); hold(recipe['settleS'])
                        phase = '定位采样'; values = hold(recipe['dwellS'],collect=True)
                        if values['count'] < 2: raise ValueError('定位窗口缺少独立的新鲜反馈')
                        visit = {'visitId':len(report['positionVisits'])+1,'cycle':cycle+1,'direction':direction,
                                 'nominalDeg':nominal,'targetDeg':target,'meanDeg':values['mean'],'stdDeg':values['std'],'count':values['count']}
                        report['positionVisits'].append(visit); capture.write('position_visit',time.time(),visit)
                        publish(True)
        elif recipe['suite']=='performance' and recipe['test']=='kt':
            for index, value in enumerate(recipe['iqLevelsA']):
                phase='电流稳定'; target=value;set_target(value);hold(recipe['settleS'])
                phase=f'Kt {index+1}';hold(recipe['dwellS'])
        else:
            phase = 'heating' if recipe['suite']=='thermal' else '指定工况'
            target=recipe['target'];set_target(target);hold(recipe['durationS'])
        if recipe['suite']=='thermal':
            result=capture.status();report['heatingResult']=result['thermal'];report['verifiedOperatingPoint']=result['continuousCandidate']
        motor._stop_output(); report['stopConfirmed']=True
        with motor.lock:motor.run.update(running=False,phase='cooling')
        if recipe['coolingS']:
            phase='cooling';target=None;hold(recipe['coolingS'],cooling=True)
        report.update(state='completed',reason='专项配方完成')
    except InterruptedError as error:
        report.update(state='stopped',reason=str(error))
    except Exception as error:
        report.update(state='interrupted',reason=str(error))
    finally:
        if sent_start and report['stopConfirmed'] is not True:
            try: motor._stop_output(); report['stopConfirmed']=True
            except Exception as error:
                report.update(state='interrupted',stopConfirmed=False,reason=f"{report['reason']}；停机未确认：{error}")
        if capture:
            try: capture.close()
            except Exception as error: report.update(state='interrupted',reason=f"{report['reason']}；记录关闭失败：{error}")
        if recording:
            try:recording.end_measurement();recording.close()
            except Exception as error:report.update(state='interrupted',reason=f"{report['reason']}；全参数记录异常：{error}")
        report.update(running=False,finishedAt=time.time());phase='结束'
        with motor.lock: motor.run.update(running=False,phase='stopped',reason=report['reason'])
        publish(True)


def start_passive(manager, data):
    recipe,subject=selected_recipe(manager,data)
    recipe['test']='capture' if recipe['suite']=='performance' else 'thermal'
    with manager.control:
        if manager.thread and manager.thread.is_alive(): raise ValueError('已有专项采集，请先结束')
        source=manager.source()
        if not fresh(source.get('motor',{})): raise ValueError('需要连接驱动板并取得新鲜反馈')
        if recipe['suite']=='performance' and not fresh(source.get('sensor',{})):
            raise ValueError('T-N 采集需要新鲜扭矩和转速')
        if recipe['suite']=='thermal' and not (source['motor'].get('snapshot') or {}).get('ntc',{}).get('valid'):
            raise ValueError('温升采集需要有效 NTC')
        report={'id':uuid.uuid4().hex,'specializedSchema':2,'category':recipe['suite'],'name':recipe['name'],
                'startedAt':time.time(),'running':True,'state':'running','phase':'采集','config':recipe,
                'sourceAtStart':source,'testSubject':subject,
                'reason':'','valid':False}
        directory=manager.root/report['id'];directory.mkdir(parents=True)
        manager.cancel=threading.Event()
        with manager.lock:manager.live=report
        manager._save(directory/'report.json',report)
        manager.thread=threading.Thread(target=run_passive,args=(manager,directory,report),daemon=True)
        try:manager.thread.start()
        except Exception:
            report.update(running=False,state='interrupted',reason='无法启动专项采集线程')
            manager._save(directory/'report.json',report)
            raise
        return manager.status()


def run_passive(manager,directory,report):
    recipe=report['config'];capture=None;began=time.monotonic();last_save=0;seen_on=False;off_at=None;recording=None
    try:
        recorder=manager.active_executor.recorder if manager.active_executor else None
        if recorder:
            recording=RecordingView(recorder,recipe['name'],recipe['ratio'],test_subject=report['testSubject']);recording.start_recording()
            report['recordingId']=recording.identifier
        capture=Capture(directory,manager.source,manager.frames,recipe,report['testSubject'])
        while time.monotonic()-began<recipe['durationS']:
            if manager.cancel.is_set():
                report.update(state='stopped',reason='操作者结束采集；设备输出仍由原控制器管理');break
            source=manager.source()
            if not fresh(source.get('motor',{})):
                if seen_on and source.get('motor',{}).get('run',{}).get('stopConfirmed') is True:
                    report['reason']='控制器已确认停止，结束专项采集';break
                raise ValueError('驱动反馈失联，原始记录保留')
            if recipe['suite']=='performance' and not fresh(source.get('sensor',{})):raise ValueError('扭矩/转速源失联')
            if recipe['suite']=='thermal' and not (source['motor'].get('snapshot') or {}).get('ntc',{}).get('valid'):
                raise ValueError('NTC 温度源无效')
            output=(source['motor'].get('snapshot') or {}).get('motor',{}).get('output_on')
            if output:seen_on=True
            if seen_on and output is False and off_at is None:
                result=capture.status();report['heatingResult']=result['thermal'];report['verifiedOperatingPoint']=result['continuousCandidate']
                off_at=time.monotonic()
            phase='cooling' if off_at is not None else 'T-N 扫描' if recipe['suite']=='performance' else 'heating'
            capture.poll(phase)
            report.update(elapsedS=time.monotonic()-began,phase=phase,**capture.status())
            with manager.lock:manager.live=json.loads(json.dumps(public_report(report)))
            if time.monotonic()-last_save>=2:
                manager._save(directory/'report.json',report);last_save=time.monotonic()
            if off_at is not None and time.monotonic()-off_at>=recipe['coolingS']:break
            if recipe['stopWhenStable'] and recipe['suite']=='thermal' and report['thermal'].get('stable'):break
            manager.cancel.wait(.1)
        else:report['reason']='达到配方采集时长'
        if report['state']=='running':report.update(state='completed',reason=report['reason'] or '工况采集完成')
    except Exception as error:report.update(state='interrupted',reason=str(error))
    finally:
        if capture:
            try:capture.close();report.update(capture.status())
            except Exception as error:report.update(state='interrupted',reason=f'记录关闭失败：{error}')
        if recording:
            try:recording.end_measurement();recording.close()
            except Exception as error:report.update(state='interrupted',reason=f"{report.get('reason','')}；全参数记录异常：{error}")
        report.update(running=False,finishedAt=time.time(),elapsedS=time.monotonic()-began,
                      valid=report['state']=='completed')
        with manager.lock:manager.live=public_report(report)
        manager._save(directory/'report.json',report)
