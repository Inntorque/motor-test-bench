"""Bounded no-load/load scripts using existing FOC modes; never changes tuning/protection."""
import csv
import json
import math
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlencode
from specialized_tests import finite, Stats
from loaded_timeline import LoadedTimeline

# Allow the 300 ms bootstrap phase and the next motor-status polling cycle.
STARTUP_TIMEOUT_S = 2.0


class ScriptEnd(Exception):
    def __init__(self, outcome, reason):
        super().__init__(reason)
        self.outcome = outcome


class AutomaticTests:
    def __init__(self, root, motor, measurements, source, poll_supplies=None):
        self.root = Path(root)
        self.motor = motor
        self.measurements = measurements
        self.source = source
        self.poll_supplies = poll_supplies
        self.lock = threading.RLock()
        self.live = None
        self.thread = None

    def active(self):
        with self.lock:
            return bool(self.live and self.live.get('running'))

    @staticmethod
    def validate(data):
        if not isinstance(data, dict) or data.get('group') not in ('no_load', 'loaded'):
            raise ValueError('请选择空载或带载脚本')
        def number(key, low, high, default=None):
            value = data.get(key,default)
            if not finite(value) or not low <= value <= high:
                raise ValueError(f'{key} 需填写 {low}～{high} 内的数值')
            return value
        result = {'group': data['group'], 'position': data['group']=='no_load' and data.get('position') is True,
                  'speed': data['group']=='no_load' and data.get('speed') is True, 'rotationMode':'continuous',
                  'manualBrakeOff': data.get('manualBrakeOff') is True,
                  'loadConfirmed': data.get('loadConfirmed') is True,
                  'currentMaxA': None if data.get('currentMaxA') is None else number('currentMaxA', .001, 5),
                  'dwellS': number('dwellS', 1, 30, 2),
                  'temperatureMaxC': None if data.get('temperatureMaxC') is None else number('temperatureMaxC', 1, 150)}
        if result['currentMaxA'] is not None and abs(result['currentMaxA']*1000-round(result['currentMaxA']*1000)) > 1e-6:
            raise ValueError('电流上限分辨率为 0.001 A')
        if data['group'] == 'no_load':
            if not result['position'] and not result['speed']:
                raise ValueError('至少选择定位或速度测试')
            if result['position']:
                reference=data.get('positionReference','absolute')
                if reference not in ('absolute','relative'):
                    raise ValueError('位置坐标方式无效')
                result['positionReference']=reference
                points = data.get('positionsDeg',[-10,0,10] if reference=='relative' else None)
                if not isinstance(points, list) or not 2 <= len(points) <= 12 or any(not finite(p) or abs(p)>200000 for p in points):
                    raise ValueError('填写 2～12 个有效位置目标点（协议数值范围 ±200000°）')
                if len(set(points)) < 2:
                    raise ValueError('定位测试至少需要两个不同目标点')
                result['positionsDeg'] = points
                result['cycles'] = number('cycles', 1, 10, 2)
                if int(result['cycles']) != result['cycles']:
                    raise ValueError('往返次数必须为整数')
                if len(points)*2*result['cycles']*result['dwellS'] > 240:
                    raise ValueError('定位阶段总保持时间需不超过 240 秒，请减少点数、次数或保持时间')
            if result['speed']:
                result['speedStepRpm'] = number('speedStepRpm', .01, 10000, 1)
                result['speedSearchDurationS'] = number('speedSearchDurationS', 1, 240, 120)
        else:
            result['loadedMode'] = data.get('loadedMode', 'ramp')
            if result['loadedMode'] not in ('ramp', 'hold', 'speed_hold'):
                raise ValueError('带载方式无效')
            if not result['loadConfirmed']:
                raise ValueError('请确认测功台已接好，并预先设置和开启制动负载')
            if result['loadedMode'] in ('hold', 'speed_hold'):
                if result['loadedMode'] == 'speed_hold':
                    result['targetSpeedRpm'] = number('targetSpeedRpm', -10000, 10000)
                    if abs(result['targetSpeedRpm']) < .1:
                        raise ValueError('定速带载目标转速幅值需不低于 0.1 rpm')
                else:
                    result['targetIqA'] = number('targetIqA', .001, 5)
                    if abs(result['targetIqA']*1000-round(result['targetIqA']*1000)) > 1e-6:
                        raise ValueError('目标电流分辨率为 0.001 A')
                result['durationS'] = number('durationS', 1, 240, 30)
                result['settleS'] = number('settleS', 0, 10, 2)
            else:
                result['currentStepA'] = number('currentStepA', .001, result['currentMaxA'] or 5, .1)
                if abs(result['currentStepA']*1000-round(result['currentStepA']*1000)) > 1e-6:
                    raise ValueError('电流步长分辨率为 0.001 A')
                if result['currentMaxA'] is not None and math.ceil(result['currentMaxA']/result['currentStepA'])*result['dwellS'] > 240:
                    raise ValueError('带载阶段总保持时间需不超过 240 秒')
        return result

    def _brake_check(self, config):
        source = self.source()
        brake = source['brake']
        known = self.measurements._fresh(brake, 3) and isinstance((brake.get('snapshot') or {}).get('status'), int)
        if config['group'] == 'no_load':
            if known and brake['snapshot']['status'] & 0x80:
                raise ValueError('空载脚本要求关闭制动输出')
            if not known and not config['manualBrakeOff']:
                raise ValueError('需设备确认制动 OFF，或现场确认励磁关闭')
        else:
            if not known or not brake['snapshot']['status'] & 0x80:
                raise ValueError('带载脚本要求制动电源已连接且实际反馈输出 ON；负载由操作者预先设置')
            if not self.measurements._fresh(source['sensor'], 1):
                raise ValueError('带载脚本要求真实扭矩传感器持续在线')
            if config.get('loadedMode') in ('hold', 'speed_hold'):
                supply = source.get('motorSupply') or {}
                if not self.measurements._fresh(supply, 3) or not ((supply.get('snapshot') or {}).get('status', 0) & 128):
                    raise ValueError('定值带载要求电机电源已连接且实际反馈输出 ON')
        return source

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.live)) if self.live else None

    def history(self):
        if not self.root.exists():
            return []
        records = []
        for path in self.root.glob('*/report.json'):
            try:
                records.append(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError, OSError):
                pass
        return sorted(records, key=lambda r:r['startedAt'], reverse=True)[:200]

    def recover_interrupted(self):
        for report in self.history():
            if report.get('running'):
                report.update(running=False, state='interrupted', outcome='service_restart', reason='服务重启，脚本中断；输出状态需重新确认', stopConfirmed=False)
                self.measurements._save(self.root/report['id']/'report.json', report)

    def file(self, test_id, name):
        import re
        if not isinstance(test_id, str) or not re.fullmatch('[a-f0-9]{32}',test_id) or name not in ('data.csv','report.json','timeline.csv','timeline.jsonl'):
            raise ValueError('无效脚本记录')
        path = self.root/test_id/name
        if not path.is_file():
            raise ValueError('文件不存在')
        return path

    def start(self, data):
        config = self.validate(data)
        motor = self.motor
        with motor.action_lock:
            if self.active() or motor.run.get('running') or (motor.thread and motor.thread.is_alive()):
                raise ValueError('已有电机任务正在运行')
            if (self.measurements.status() or {}).get('state') in ('running','settling'):
                raise ValueError('先结束当前被动专项采集，再启动自动脚本')
            source = self._brake_check(config)
            snapshot = motor._refresh()
            motor._healthy(snapshot, starting=True)
            device_current = motor._limit()
            # /api/motor_profile changes the selected profile and is POST-only.
            # Read the active profile already supplied by the read-only status.
            profile = (snapshot.get('motorProfile') or {}).get('nameplate') or {}
            ratio = profile.get('reduction_ratio')
            if not finite(ratio) or ratio <= 0:
                raise ValueError('未取得有效减速比')
            speed_limit = motor._request('/api/get?id=11').get('value')
            if not finite(speed_limit) or speed_limit <= 0:
                raise ValueError('无法确认设备当前速度上限')
            current_source='device' if config['currentMaxA'] is None else 'operator'
            config['currentMaxA']=device_current if config['currentMaxA'] is None else config['currentMaxA']
            if config['currentMaxA'] > device_current:
                raise ValueError(f'本次电流上限超过设备当前 {device_current:g} A 限值')
            feedback = snapshot['feedback']
            if feedback.get('position_stale') is not False or not finite(feedback.get('position_deg')):
                raise ValueError('没有新鲜多圈位置反馈，不能记录位置或执行定位')
            origin = feedback['position_deg']/ratio
            targets=[p+origin if config.get('positionReference')=='relative' else p for p in config.get('positionsDeg',[])]
            if any(abs(p)>200000 or abs(p*ratio*1000)>2147483647 for p in targets):
                raise ValueError('位置目标换算后超出位置命令协议范围')
            if config['position'] and (abs(origin)>200000 or abs(origin*ratio*1000)>2147483647):
                raise ValueError('当前多圈坐标超出位置命令协议可表示范围')
            if not finite((snapshot.get('ntc') or {}).get('temp_c')):
                raise ValueError('没有有效 NTC 温度')
            device_temperature=snapshot['ntc'].get('limit_c')
            temperature_source='device' if config['temperatureMaxC'] is None else 'operator'
            if config['temperatureMaxC'] is None:
                if not finite(device_temperature) or not 1<=device_temperature<=150:
                    raise ValueError('未读到设备 NTC 保护温度，请连接设备或在保护设置中填写停止温度')
                config['temperatureMaxC']=device_temperature
            elif finite(device_temperature) and config['temperatureMaxC']>device_temperature:
                raise ValueError(f'温度停止值不能高于设备保护温度 {device_temperature:g} °C')
            if snapshot['ntc']['temp_c'] >= config['temperatureMaxC']:
                raise ValueError('当前 NTC 已达到本次温度上限')
            if not finite(feedback.get('speed_rpm')):
                raise ValueError('没有有效速度反馈')
            if config.get('loadedMode')=='hold' and config['targetIqA']>config['currentMaxA']:
                raise ValueError('指定 Iq 超过当前电流停止值或设备限值')
            if config.get('loadedMode')=='speed_hold' and abs(config['targetSpeedRpm'])*ratio>min(300,speed_limit):
                raise ValueError('指定输出转速超过当前设备速度边界')
            if config.get('loadedMode')=='ramp' and (config['currentStepA']>config['currentMaxA'] or math.ceil(config['currentMaxA']/config['currentStepA'])*config['dwellS']>240):
                raise ValueError('带载步长不能超过当前电流范围，累计保持时间不能超过 240 秒')
            motor.cancel = threading.Event()
            motor.lease = time.monotonic()
            report = {'id':uuid.uuid4().hex,'startedAt':time.time(),'config':config,'running':True,
                      'state':'starting','phase':'准备','outcome':None,'reason':'','stopConfirmed':None,
                      'ratio':ratio,'originOutputDeg':origin,'deviceCurrentLimitA':device_current,
                      'positionTargetsDeg':targets,'deviceTemperatureLimitC':device_temperature,
                      'protectionSources':{'current':current_source,'temperature':temperature_source},
                      'deviceSpeedLimitRotorRpm':speed_limit,'motorVersion':snapshot.get('version'),
                      'controllerSpeedCeilingRpm':(speed_limit if config.get('loadedMode')=='hold' else min(300,speed_limit))/ratio,
                      'steps':[],'positionResults':[],'peakActualIqA':0,'peakOutputSpeedRpm':0,
                      'initialNtcC':snapshot['ntc']['temp_c'],'lastNtcC':snapshot['ntc']['temp_c'],
                      'temperatureChangeC':0,'peakNtcC':snapshot['ntc']['temp_c'],'protectionSeen':False,
                      'sourceBrakeSettings':(source['brake'].get('snapshot') or {}).get('settings'),
                      'sourceAtStart':source,
                      'notes':['位置为现有编码器换算的上电相对输出角度，未清零/校准，非外部角度基准的绝对精度验收。',
                               '台架已确认可连续旋转；无软件机械角度限位，定位仍按目标点往返。',
                               '定位误差和速度偏差仅作实测统计，不设合格误差，不因偏差数值中止测试。',
                               '沿用当前固件 PID、限幅、硬件保护；电流阈值和设备速度边界作后台监测，不能代替硬件瞬态保护。',
                               '控制器限幅、时间预算或停止阈值不能当作电机极限；只有设备实际报告保护才标为保护触发。',
                               '最大转矩为本次工况测得值，非机械额定极限或硬件 STO 测试。']}
            if config['speed']:
                report['speedSearch'] = {'state':'pending','maxMeanRpm':None,'maxMeanTargetRpm':None,
                                         'platformTargetRpm':None,'limitFound':False,
                                         'controllerCeilingRpm':report['controllerSpeedCeilingRpm']}
            directory=self.root/report['id'];directory.mkdir(parents=True,exist_ok=False)
            self.measurements._save(directory/'report.json',report)
            with self.lock:
                self.live=report
            motor.run={'phase':'automatic','running':True,'scriptId':report['id'],'targetA':None,'steps':0,'elapsed':0,'stopConfirmed':None,
                       'acquisitionManaged':config.get('loadedMode') in ('hold','speed_hold')}
            self.thread=threading.Thread(target=self._run,args=(directory,report),daemon=True)
            motor.thread=self.thread
            try:
                self.thread.start()
            except Exception:
                motor.run['running']=False
                with self.lock:self.live.update(running=False,state='interrupted',reason='无法启动任务线程')
                self.measurements._save(directory/'report.json',self.live)
                raise
            return self.status()

    def stop(self, test_id):
        if self.live and test_id != self.live['id']:
            raise ValueError('当前脚本已变化，请刷新后重试')
        self.motor.cancel.set()
        if self.thread:
            self.thread.join(5)
            if self.thread.is_alive():
                raise ValueError('正在等待停机确认，请勿重新启动')
        return self.status()

    def _run(self,directory,report):
        motor=self.motor;config=report['config'];ratio=report['ratio'];began=time.monotonic()
        target=None;mode=None;phase='准备';recording_id=None;last_saved=0;drive_stopped=None;enabled_at=0;stage_deadline=None
        source_brake=report['sourceBrakeSettings'];csv_file=None
        timeline=None;poll_cancel=threading.Event();poll_thread=None;poll_errors=[]
        fixed=config.get('loadedMode') in ('hold','speed_hold')
        initial_motor_supply=(report.get('sourceAtStart',{}).get('motorSupply',{}).get('snapshot') or {}).get('settings')
        def poll_supplies():
            try:
                while not poll_cancel.is_set():
                    self.poll_supplies()
                    poll_cancel.wait(.25)
            except Exception as error:
                poll_errors.append(str(error))
        def publish(save=False):
            report.update(elapsedS=time.monotonic()-began,phase=phase)
            if timeline:report['timeline']=timeline.status()
            with self.lock:self.live=json.loads(json.dumps(report))
            with motor.lock:motor.run.update(elapsed=report['elapsedS'],reason=report.get('reason',''))
            with motor.lock:motor.run['steps']=len(report['steps'])
            if save:self.measurements._save(directory/'report.json',report)
        def request(route):
            try:
                reply=motor._request(route,write=True)
            except Exception as error:
                if timeline:timeline.event('command_error',{'route':route,'error':str(error)})
                raise
            if timeline:timeline.event('command_ack',{'route':route,'response':reply})
            return reply
        def command(field,value):
            if motor.cancel.is_set():raise ScriptEnd('operator_stop','操作者停止')
            request('/api/control/set?'+urlencode({'field':field,'value':value}))
            if field=='current_a':
                with motor.lock:motor.run['targetA']=value
        def sample():
            nonlocal last_saved
            if motor.cancel.is_set():raise ScriptEnd('operator_stop','操作者停止')
            if time.monotonic()-motor.lease > 2:raise ScriptEnd('page_timeout','工作台页面心跳超时')
            if timeline and timeline.error:raise ScriptEnd('recording_error',timeline.error)
            if poll_errors:raise ScriptEnd('power_supply_lost',poll_errors[0])
            if stage_deadline is not None and time.monotonic()>stage_deadline:
                raise ScriptEnd('stage_timeout','本阶段超过累计保持时间及命令开销预算')
            snapshot=motor._refresh()
            ntc=snapshot.get('ntc') or {};state=snapshot['motor'];feedback=snapshot['feedback']
            temperature=ntc.get('temp_c')
            if state.get('fault') or state.get('state')==3 or ntc.get('tripped') or ntc.get('tripped_latched'):
                report.update(protectionSeen=True,protectionObservedAt=time.time())
                raise ScriptEnd('protection','设备实际报告保护，停止递增并结束脚本')
            motor._healthy(snapshot)
            if mode is not None and (state.get('mode')!=mode or state.get('state') not in (1,2) or not state.get('output_on')):
                raise ScriptEnd('unexpected_stop','电机意外停止或运行模式改变')
            if mode is not None and state.get('state')==1 and time.monotonic()-enabled_at>STARTUP_TIMEOUT_S:
                raise ScriptEnd('start_timeout','电机预充电后未进入运行状态')
            if feedback.get('position_stale') is not False or not finite(feedback.get('position_deg')) or not finite(feedback.get('speed_rpm')):
                raise ScriptEnd('feedback_lost','位置/速度反馈无效或过期')
            if not finite(feedback.get('iq_a')) or not finite(feedback.get('id_a')):
                raise ScriptEnd('feedback_lost','电流反馈不是有效有限数值')
            pos=feedback['position_deg']/ratio;speed=feedback['speed_rpm']/ratio;iq=feedback['iq_a']
            if not finite(temperature) or temperature>=config['temperatureMaxC']:
                raise ScriptEnd('temperature_limit','达到本次温度监测上限')
            if abs(iq)>config['currentMaxA']+.001:raise ScriptEnd('current_limit','达到本次软件电流监测上限')
            if abs(speed)>report['controllerSpeedCeilingRpm']*1.05+.1:
                raise ScriptEnd('controller_speed_guard','实际转速超出设备速度边界，停止测试')
            source=self._brake_check(config)
            if config['group']=='loaded' and (source['brake'].get('snapshot') or {}).get('settings')!=source_brake:
                raise ScriptEnd('load_changed','带载测试期间制动设定改变')
            if fixed and (source['motorSupply'].get('snapshot') or {}).get('settings')!=initial_motor_supply:
                raise ScriptEnd('supply_changed','带载测试期间电机电源设定改变')
            sensor=(source['sensor'].get('snapshot') or {}) if source['sensor'].get('connected') else {}
            if config['group']=='loaded':
                raw=self.measurements.status() or {}
                if recording_id and raw.get('state') not in ('running','settling'):
                    raise ScriptEnd('torque_recording_lost',raw.get('reason','扭矩采集中断'))
                if not finite(sensor.get('torque')) or abs(sensor['torque'])>=50:
                    raise ScriptEnd('torque_limit','扭矩无效或达到本台 50 N·m 量程边界')
            report['peakActualIqA']=max(report['peakActualIqA'],abs(iq))
            report['peakOutputSpeedRpm']=max(report['peakOutputSpeedRpm'],abs(speed))
            report['peakNtcC']=max(report['peakNtcC'],temperature)
            report.update(lastNtcC=temperature,temperatureChangeC=temperature-report['initialNtcC'])
            row={'t':time.monotonic()-began,'position':pos,'speed':speed,'iq':iq,'temp':temperature,
                 'positionAt':feedback.get('position_at'),'feedbackAt':feedback.get('updated_at')}
            writer.writerow([row['t'],time.time(),phase,mode,target,pos,speed,iq,feedback.get('id_a'),temperature,sensor.get('torque'),sensor.get('speed')])
            if row['t']-last_saved>=.5:
                publish(save=True);csv_file.flush();last_saved=row['t']
            return row
        def hold(seconds, search_deadline=None):
            rows=[];end=time.monotonic()+seconds
            while time.monotonic()<end:
                if search_deadline is not None and time.monotonic()>=search_deadline:
                    raise ScriptEnd('speed_search_timeout','升速搜索时间已用完，尚未确认电机速度上限')
                rows.append(sample());motor.cancel.wait(.1)
            if search_deadline is not None and time.monotonic()>=search_deadline:
                raise ScriptEnd('speed_search_timeout','升速搜索时间已用完，尚未确认电机速度上限')
            return rows
        def enable(which,seconds):
            nonlocal mode,enabled_at,stage_deadline
            mode=which;enabled_at=time.monotonic();stage_deadline=enabled_at+seconds+10
            request(f'/api/motor?enable=1&mode={which}&seconds={min(255,math.ceil(seconds+15))}')
        def disable():
            nonlocal mode,drive_stopped,stage_deadline
            def log_stop(route,reply):
                try:timeline.event('command_ack',{'route':route,'response':reply})
                except Exception as error:timeline.error=str(error)
            if timeline:motor._stop_output(on_command=log_stop)
            else:motor._stop_output()
            mode=None;stage_deadline=None
            if drive_stopped is None or config['group']!='loaded':drive_stopped=time.time()
            if timeline:
                try:timeline.event('motor_stop_confirmed',motor.status())
                except Exception as error:timeline.error=str(error)
        def speed_window(attempt, search_deadline):
            rows=hold(config['dwellS'],search_deadline)
            window=list({r['feedbackAt']:r for r in rows if r['t']>=rows[-1]['t']-config['dwellS']/2}.values())
            if len(window)<2 or any(r['feedbackAt'] is None for r in window):
                raise ScriptEnd('feedback_insufficient','速度末段缺少足够独立的新反馈')
            stats=Stats()
            for row in window:stats.add(row['speed'])
            values=stats.result();mean=values['mean']
            # Whole rotor-RPM samples may use the older coarse feedback format.
            floor=(1 if all(abs(r['speed']*ratio-round(r['speed']*ratio))<1e-6 for r in window) else .01)/ratio
            item={'targetRpm':target,'meanOutputRpm':mean,'errorRpm':mean-target,
                  'speedStdRpm':values['std'],'peakToPeakRpm':values['peakToPeak'],
                  'errorRmsRpm':math.sqrt(values['std']**2+(mean-target)**2),
                  'comparisonFloorRpm':floor,'attempt':attempt,'samples':values['count']}
            report['steps'].append(item)
            search=report['speedSearch']
            if search['maxMeanRpm'] is None or mean>search['maxMeanRpm']:
                search.update(maxMeanRpm=mean,maxMeanTargetRpm=target)
            return item
        try:
            if fixed:
                timeline=LoadedTimeline(directory,self.source,self.measurements.frames,ratio,
                                        {'config':config,'sourceAtStart':report['sourceAtStart'],'ratio':ratio})
                if self.poll_supplies:
                    poll_thread=threading.Thread(target=poll_supplies,daemon=True);poll_thread.start()
            csv_file=(directory/'data.csv').open('w',newline='',encoding='utf-8-sig')
            writer=csv.writer(csv_file)
            writer.writerow(['elapsed_s','pc_received_unix_s','phase','mode','target','output_position_deg','output_speed_rpm','actual_iq_A','actual_id_A','motor_ntc_C','torque_sensor_Nm','torque_sensor_rpm'])
            request('/api/telem?enable=1&period_ms=20')
            sample()
            if config['group']=='no_load':
                if config['position']:
                    phase='空载定位往返';target=report['originOutputDeg'];command('position_deg',target)
                    route=[]
                    for cycle in range(int(config['cycles'])):
                        route.extend((cycle,p,'forward') for p in report['positionTargetsDeg'])
                        route.extend((cycle,p,'reverse') for p in reversed(report['positionTargetsDeg']))
                    enable(3,len(route)*config['dwellS'])
                    for cycle,point,direction in route:
                        target=point;command('position_deg',target);rows=hold(config['dwellS'])
                        settled=list({r['positionAt']:r for r in rows if r['t']>=rows[-1]['t']-.4}.values())
                        times={r['positionAt'] for r in settled}
                        if None in times or len(times)<2:raise ScriptEnd('feedback_insufficient','定位末段缺少足够新鲜位置反馈')
                        average=sum(r['position'] for r in settled)/len(settled)
                        positions=Stats();errors=Stats()
                        for row in settled:
                            positions.add(row['position']);errors.add(row['position']-point)
                        position_stats=positions.result();error_stats=errors.result()
                        item={'cycle':cycle+1,'targetDeg':point,'direction':direction,'meanOutputDeg':average,
                              'errorDeg':average-point,'endErrorDeg':settled[-1]['position']-point,
                              'positionStdDeg':position_stats['std'],'positionPeakToPeakDeg':position_stats['peakToPeak'],
                              'errorRmsDeg':error_stats['rms'],'maxAbsErrorDeg':max(abs(error_stats['min']),abs(error_stats['max'])),
                              'samples':len(times)}
                        report['steps'].append(item);publish(save=True)
                    disable()
                    for point in report['positionTargetsDeg']:
                        for direction in ('forward','reverse'):
                            values=[r['meanOutputDeg'] for r in report['steps'] if r.get('targetDeg')==point and r.get('direction')==direction]
                            stat=Stats()
                            for value in values:stat.add(value)
                            report['positionResults'].append({'targetDeg':point,'direction':direction,'statistics':stat.result(),'repeats':len(values)})
                if config['speed']:
                    phase='空载最高转速搜索';target=0;command('speed_rpm',0)
                    search=report['speedSearch'];search['state']='running'
                    search_deadline=time.monotonic()+config['speedSearchDurationS']
                    ceiling=report['controllerSpeedCeilingRpm']
                    enable(2,config['speedSearchDurationS'])
                    level=0
                    while True:
                        level+=1
                        target=min(ceiling,level*config['speedStepRpm'])
                        search['currentTargetRpm']=target
                        command('speed_rpm',min(300,report['deviceSpeedLimitRotorRpm'],target*ratio))
                        item=speed_window(1,search_deadline)
                        band=max(item['comparisonFloorRpm'],3*item['speedStdRpm']/math.sqrt(item['samples']))
                        item['comparisonBandRpm']=band;publish(save=True)
                        # Detect a repeated response plateau from measured scatter; no acceptance error.
                        if target<ceiling-1e-9 and band<config['speedStepRpm']/2 and item['meanOutputRpm']>band and -item['errorRpm']>band:
                            repeated=speed_window(2,search_deadline)
                            combined=max(item['comparisonFloorRpm'],repeated['comparisonFloorRpm'],3*math.sqrt(item['speedStdRpm']**2/item['samples']+repeated['speedStdRpm']**2/repeated['samples']))
                            repeated['comparisonBandRpm']=combined;publish(save=True)
                            if combined<config['speedStepRpm']/2 and repeated['meanOutputRpm']>combined and -repeated['errorRpm']>combined and abs(repeated['meanOutputRpm']-item['meanOutputRpm'])<=combined:
                                search.update(limitFound=True,state='plateau_observed',platformTargetRpm=target)
                                raise ScriptEnd('speed_plateau','重复采样观察到当前工况转速平台，保留实测均值、偏差和波动；不作合格判定')
                        if target>=ceiling-1e-9:
                            raise ScriptEnd('controller_limit','已到控制器允许的速度范围，尚未测出电机自身转速上限')
            elif fixed:
                speed_hold=config['loadedMode']=='speed_hold'
                field='speed_rpm' if speed_hold else 'current_a'
                target=config['targetSpeedRpm'] if speed_hold else config['targetIqA']
                phase='低速闭环带载启动' if speed_hold else '指定 Iq 带载启动'
                command(field,0);enable(2 if speed_hold else 5,config['durationS']+config['settleS']+2)
                startup_deadline=time.monotonic()+STARTUP_TIMEOUT_S
                while sample() and (motor.snapshot.get('motor') or {}).get('state')!=2:
                    if time.monotonic()>=startup_deadline:raise ScriptEnd('start_timeout','电机未进入运行状态')
                    motor.cancel.wait(.02)
                command(field,target*ratio if speed_hold else target)
                phase='低速闭环运行等待' if speed_hold else '指定 Iq 运行等待';hold(config['settleS'])
                timeline.start_recording()
                report['recordingStartedAt']=timeline.recording_at
                phase='恒速带载 · 全参数时间序列记录' if speed_hold else '恒流带载 · 全参数时间序列记录';publish(save=True)
                hold(config['durationS'])
                timeline.end_measurement()
                report['recordingEndedAt']=timeline.measurement_end
                report['steps'].append({'targetSpeedRpm' if speed_hold else 'targetIqA':target,'durationS':timeline.measurement_end-timeline.recording_at})
                report.update(state='completed',outcome='duration_complete',reason='定值带载记录时长完成，保存全部已接入参数的时间序列')
                disable()
            else:
                phase='带载最大转矩/保护触发';target=0
                count=math.ceil(config['currentMaxA']/config['currentStepA'])
                measurement=self.measurements.start({'kind':'loaded_peak','durationS':min(3600,count*config['dwellS']+30),'name':'自动带载逐帧扭矩记录'})
                recording_id=measurement['id'];report['rawTorqueRecordId']=recording_id
                command('current_a',0);enable(5,count*config['dwellS'])
                for i in range(count):
                    target=min(config['currentMaxA'],round((i+1)*config['currentStepA'],3));command('current_a',target)
                    hold(config['dwellS'])
                    raw=self.measurements.status()
                    if raw['state'] not in ('running','settling'):raise ScriptEnd('torque_recording_lost',raw.get('reason','扭矩记录中断'))
                    stat=raw.get('stats',{}).get('torque',{})
                    peak=max(abs(stat.get('min') or 0),abs(stat.get('max') or 0))
                    report['steps'].append({'targetIqA':target,'peakObservedTorqueNm':peak});publish(save=True)
                disable()
            if not fixed:report.update(state='completed',outcome='configured_limit',reason='顺序脚本完成，达到本次设定范围；未据此认定电机额定或保护极限')
        except ScriptEnd as end:
            terminal='completed' if end.outcome=='speed_plateau' else 'stopped' if end.outcome in ('operator_stop','protection','controller_limit','speed_search_timeout') else 'interrupted'
            report.update(state=terminal,outcome=end.outcome,reason=str(end))
        except Exception as error:
            report.update(state='stopped' if motor.cancel.is_set() else 'interrupted',
                          outcome='operator_stop' if motor.cancel.is_set() else 'error',
                          reason='操作者停止' if motor.cancel.is_set() else str(error))
        finally:
            search=report.get('speedSearch')
            if search and search['state']!='pending':
                search.update(stopCause=report['outcome'])
                if search['state']=='running':
                    search['state']='controller_limited' if report['outcome']=='controller_limit' else 'stopped'
            motor.cancel.set()
            if timeline:
                try:
                    timeline.event('terminal_observation',{'outcome':report.get('outcome'),'reason':report.get('reason'),'sources':self.source()})
                    timeline.end_measurement()
                except Exception as error:timeline.error=str(error)
            try:
                disable();report['stopConfirmed']=True
            except Exception as error:
                report.update(stopConfirmed=False,state='interrupted',reason=f"{report['reason']}；停机未确认：{error}")
                with motor.lock:motor.connected=False;motor.error=report['reason']
            if csv_file:
                try:csv_file.close()
                except OSError as error:
                    report.update(state='interrupted',reason=f"{report['reason']}；电机记录写入失败：{error}")
            if timeline:
                try:
                    # Read-only tail captures the confirmed OFF state and residual torque.
                    timeline.phase='post_stop';time.sleep(1)
                    timeline.close()
                    result=timeline.status();report['timeline']=result
                    report['recordingStartedAt']=result['recordingStartedAt']
                    report['recordingEndedAt']=result['recordingEndedAt']
                    torque=result['statistics']['torqueNm']
                    report['measuredPeakTorqueNm']=max(abs(torque['min']),abs(torque['max'])) if torque.get('count') else None
                    if report['steps']:report['steps'][-1]['peakObservedTorqueNm']=report['measuredPeakTorqueNm']
                    if not torque.get('count'):raise ValueError('记录窗口没有有效扭矩数据')
                except Exception as error:
                    report.update(state='interrupted',reason=f"{report['reason']}；完整记录异常：{error}")
                finally:
                    report['timeline']=timeline.status()
            poll_cancel.set()
            if poll_thread:poll_thread.join(3)
            try:
                if recording_id:
                    self._finish_torque(recording_id, report, drive_stopped)
            except Exception as error:
                report['rawTorqueAnalysisError']=str(error)
                report.update(state='interrupted',reason=f"{report['reason']}；扭矩记录收尾失败：{error}")
            report.update(running=False,finishedAt=time.time())
            with motor.lock:
                motor.run.update(running=False,phase='stopped',acquisitionManaged=False,reason=report.get('reason',''),stopConfirmed=report['stopConfirmed'],protectionSeen=report['protectionSeen'])
            try:publish(save=True)
            except OSError as error:
                report.update(state='interrupted',reason=f"{report['reason']}；报告保存失败：{error}")
                publish()

    def _finish_torque(self, recording_id, report, drive_stopped):
        # Keep passive torque acquisition briefly after STOP; never restart the motor.
        time.sleep(1)
        raw=self.measurements.stop(recording_id)
        report['rawTorqueState']=raw['state'];report['rawTorqueReason']=raw['reason']
        peak=None;after=[]
        try:
            with self.measurements.file(recording_id,'data.csv').open(encoding='utf-8-sig') as stream:
                for row in csv.DictReader(stream):
                    if row['sample_type']!='sensor' or not row['torque_Nm']:continue
                    torque=abs(float(row['torque_Nm']));stamp=float(row['pc_received_unix_s'])
                    if drive_stopped and stamp>drive_stopped:after.append(torque)
                    else:peak=torque if peak is None else max(peak,torque)
            report['measuredPeakTorqueNm']=peak
            report['postStopTorqueAbsMaxNm']=max(after) if after else None
            report['postStopWindowS']=1
            report['rawTorqueSamples']=raw.get('sensorSamples',0)
            if raw['state']=='interrupted' or peak is None:
                report.update(state='interrupted',reason=f"{report['reason']}；扭矩记录不完整：{raw.get('reason','无有效帧')}")
        except Exception as error:
            report['rawTorqueAnalysisError']=str(error)
            report.update(state='interrupted',reason=f"{report['reason']}；扭矩原始数据分析失败：{error}")
