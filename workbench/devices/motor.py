"""Native MBP motor client. Read methods consume receive caches only."""
import copy
import secrets
import struct
import threading
import time
import zlib
from protocol import generated as p
from protocol.codec import IsoTp,SampleAssembler,ProtocolError,packet,unpack,objects,values,tlv,decode_tlvs
from .can_bus import CanBusManager
from workbench_config import local_config,ROOT
import json

RESULTS=['OK','PENDING','UNSUPPORTED','INVALID_ARGUMENT','OUT_OF_RANGE','NOT_READY','BUSY','STALE_SESSION','STALE_GENERATION','DEVICE_FAULT','TIMEOUT']

class MotorDevice:
    def __init__(self,bus_factory=None,observe=None):
        self.bus_factory=bus_factory;self.observe=observe
        self.lock=threading.RLock();self.condition=threading.Condition(self.lock)
        self.transactions=[threading.Lock(),threading.Lock()];self.channels=[];self.pending={}
        self.bus=None;self.closed=threading.Event();self.thread=None;self.diagnostic_thread=None;self.identity={};self.sample={};self.field_times={}
        self.state={};self.state_at=0;self.state_utc=0;self.sample_at=0;self.sample_utc=0
        self.assembler=SampleAssembler();self.error='';self.boot_id=0;self.session_id=0;self.generation=0;self.command_seq=0
        self.request_id=secrets.randbelow(1<<30)+1;self.wire=None;self.identified_at=0;self.identified_utc=0;self.boot_tag=None;self.uptime=0;self.maximum=None;self.last_fault_event=None
        self.profile=json.loads((ROOT/'profiles/bench.json').read_text(encoding='utf-8'))
        self.node=int(local_config().get('motor',{}).get('nativeNode',1))
        if not 1<=self.node<=63:raise ValueError('MBP nativeNode must be 1..63')
    def _event(self,kind,payload):
        if self.observe:self.observe(kind,_json_objects({'deviceUid':self.identity.get(p.OBJ_DEVICE_UID,b'').hex(),'bootId':str(self.boot_id),'receivedMonotonic':time.monotonic(),**payload}))
    def _open(self):
        if self.bus and not self.bus.closed.is_set():return
        if self.bus:self.close()
        if self.bus_factory:raw=self.bus_factory()
        else:
            from .zqwl import ZqwlBus,find_zqwl_port
            config=local_config().get('motor',{})
            if config.get('interface','zqwl')!='zqwl' or int(config.get('baud',500000))!=500000:
                raise OSError('首版支持 ZQWL / 500000；请核对 motor 配置')
            channel=config.get('channel') or find_zqwl_port()
            if not channel:raise OSError('未找到 ZQWL USB-CAN 适配器')
            raw=ZqwlBus(channel,bitrate=500000)
        self.closed.clear();self.bus=CanBusManager(raw,self._receive_safe,self._failed)
        self.channels=[IsoTp(lambda d,ch=i:self.bus.send((p.CAN_REQUEST if ch==0 else p.CAN_DIAGNOSTIC_REQUEST)+self.node,d,1 if ch==0 else 3),lambda d,ch=i:self._response(ch,d),1024 if i==0 else 256) for i in range(2)]
        self.thread=threading.Thread(target=self._poll,name='mbp-protocol',daemon=True);self.thread.start()
    def _failed(self,message):
        with self.condition:
            self.error=message;self.session_id=0
            for item in self.pending.values():item['error']=message
            self.condition.notify_all()
    def _receive_safe(self,identifier,data):
        try:self._receive(identifier,data)
        except (ProtocolError,UnicodeError,struct.error) as error:
            self._event('motor_protocol_rejected',{'id':identifier,'data':data.hex(),'error':str(error)})

    def _receive(self,identifier,data):
        now=time.monotonic();stamp=time.time();base=identifier-self.node
        with self.condition:
            if base==p.CAN_RESPONSE or base==p.CAN_DIAGNOSTIC_RESPONSE:
                if self.channels:self.channels[base==p.CAN_DIAGNOSTIC_RESPONSE].receive(data,now)
            elif base==p.CAN_STATUS and len(data)==8:
                if self.wire and self.wire[0]!=p.MAJOR:return
                state,mode,flags,stop_id=struct.unpack('<BBHI',data)
                if state>4 or mode>5 or flags&~63:return
                self.state={'state':state,'mode':mode,'flags':flags,'lastStopId':stop_id};self.state_at=now;self.state_utc=stamp
                self.condition.notify_all()
            elif base==p.CAN_DEVICE_HEARTBEAT and len(data)==8:
                tag,uptime=struct.unpack('<II',data)
                if self.boot_tag is not None and (tag!=self.boot_tag or ((uptime-self.uptime)&0xffffffff)>0x80000000):
                    self.identity={};self.sample={};self.field_times={};self.state={};self.state_at=0;self.sample_at=0;self.boot_id=0;self.session_id=0;self.last_fault_event=None;self.error='驱动板已重启，请重新连接识别'
                    for item in self.pending.values():item['error']=self.error
                self.boot_tag,self.uptime=tag,uptime
            elif base==p.CAN_SAMPLE:
                group=self.assembler.receive(data)
                if group:
                    group.update(receivedMonotonic=now,receivedAt=stamp,bootId=str(self.boot_id),deviceUid=self.identity.get(p.OBJ_DEVICE_UID,b'').hex(),recordSchema=2,runGeneration=self.generation,sessionId=str(self.session_id))
                    self.sample=group;self.sample_at=now;self.sample_utc=stamp
                    for f in group['fields']:self.field_times[f]=(now,stamp)
                    self._event('motor_snapshot',group)
            elif base==p.CAN_EVENT and len(data)==8:
                code,severity,source,device_ms=struct.unpack('<HBBI',data)
                event=dict(code=code,severity=severity,source=source,deviceMs=device_ms,receivedAt=stamp)
                if severity==2:
                    self.last_fault_event=event
                    if source==1:self.identity[p.OBJ_VENDOR_FAULT_CODE]=code
                self._event('motor_event',event)
    def _response(self,ch,data):
        major,minor,op,flags,rid,body=unpack(data)
        item=self.pending.get(rid)
        if not item or item['channel']!=ch or item['opcode']!=op:return
        if not flags&1 or len(body)<4:raise ProtocolError('invalid response')
        rc,stage,reserved=struct.unpack_from('<HBB',body)
        if reserved or stage>2 or rc>=len(RESULTS):raise ProtocolError('invalid result')
        decoded=decode_tlvs(body[4:])
        dictionary=p.COMPOUNDS['transfer_response'] if op==p.OP_READ_TRANSFER else p.OBJECTS
        for oid,(typ,value) in decoded.items():
            if major!=p.MAJOR and oid>5:continue
            if oid in dictionary and typ!=dictionary[oid][1]:raise ProtocolError('response object type')
            if op!=p.OP_READ_TRANSFER and oid in p.OBJECTS and typ==p.TYPE_NESTED:
                compound=p.COMPOUNDS.get(p.OBJECTS[oid][0],{})
                for field,(field_type,_) in value.items():
                    if field in compound and field_type!=compound[field][1]:raise ProtocolError('compound field type')
        item.update(response=(rc,stage,{oid:value for oid,(_,value) in decoded.items()}),wire=(major,minor));self.condition.notify_all()
    def _poll(self):
        next_hb=0;next_diag=0
        while not self.closed.wait(.001):
            try:
                now=time.monotonic()
                with self.condition:
                    for channel in self.channels:channel.poll(now)
                    if self.session_id and now>=next_hb:
                        self.bus.send(p.CAN_CONTROL_HEARTBEAT+self.node,struct.pack('<Q',self.session_id),1);next_hb=now+.25
                # Optional diagnostics use a separate task and transport lock.
                if now>=next_diag:
                    next_diag=now+1
                    if self.identity and (not self.diagnostic_thread or not self.diagnostic_thread.is_alive()):
                        self.diagnostic_thread=threading.Thread(target=self._diagnostics,name='mbp-diagnostics',daemon=True)
                        self.diagnostic_thread.start()
            except (OSError,ProtocolError) as error:self._failed(str(error));return
    def _diagnostics(self):
        try:
            if self.wire and self.wire[0]!=p.MAJOR:
                _,_,identity=self.request(p.OP_IDENTIFY)
                with self.lock:
                    self.identity=identity;self.identified_at=time.monotonic();self.identified_utc=time.time()
                return
            _,_,objs=self.request(p.OP_READ_OBJECTS)
            with self.lock:self.identity.update(objs)
            if self.identity.get(p.OBJ_OPTIONAL_FEATURES,0)&8:
                _,_,v=self.request(p.OP_READ_DIAGNOSTIC,struct.pack('<H',1),channel=1)
                state=v.get(p.OBJ_MAXIMUM_STATE,3);window=v.get(p.OBJ_MAXIMUM_WINDOW)
                self.maximum={'state':state,'window':window,'updated_at':time.time()}
                self._event('maximum_output_window',self.maximum)
            if p.OBJ_DIAGNOSTIC_DESCRIPTOR in self.identity:
                self._event('coherent_control_diagnostic',self.read_diagnostic(2))
        except (OSError,ValueError) as error:self._event('motor_diagnostic_error',{'error':str(error)})
    def request(self,opcode,body=b'',channel=0,mutation=False):
        with self.transactions[channel]:
            with self.condition:
                if not self.bus or self.error:raise OSError(self.error or '电机未连接')
                if mutation:
                    if not self.session_id:raise ProtocolError('没有有效驱动控制会话')
                    self.command_seq+=1;body=struct.pack('<QQII',self.boot_id,self.session_id,self.generation,self.command_seq)+body
                self.request_id=(self.request_id+1)&0xffffffff or 1;rid=self.request_id
                item={'channel':channel,'opcode':opcode};self.pending[rid]=item
                self.channels[channel].start(packet(opcode,rid,body),time.monotonic())
                self._event('motor_command_request',dict(requestId=rid,opcode=opcode,body=body.hex(),bootId=str(self.boot_id),sessionId=str(self.session_id),generation=self.generation,commandSeq=self.command_seq))
                deadline=time.monotonic()+3
                try:
                    while 'response' not in item and 'error' not in item:
                        remaining=deadline-time.monotonic()
                        if remaining<=0:raise TimeoutError(f'MBP {opcode} 应答超时；不自动重发动作')
                        self.condition.wait(remaining)
                    if 'error' in item:raise OSError(item['error'])
                    self.wire=item['wire'];rc,stage,result=item['response']
                    self._event('motor_command_response',dict(requestId=rid,result=RESULTS[rc],stage=stage,objects=_json_objects(result)))
                    if rc>1:raise ProtocolError(f'驱动板拒绝：{RESULTS[rc]}')
                    return rc,stage,result
                finally:self.pending.pop(rid,None)
    def connect(self):
        self._open()
        with self.lock:self.error=''
        _,_,identity=self.request(p.OP_IDENTIFY)
        required=(p.OBJ_DEVICE_UID,p.OBJ_BOOT_ID)
        if self.wire[0]==p.MAJOR:required+=(p.OBJ_CONTROL_MODES,p.OBJ_FEEDBACK_FIELDS,p.OBJ_DQ_CURRENT_CONVENTION)
        if any(k not in identity for k in required):raise ProtocolError('缺少 MBP 核心身份/能力对象')
        if len(identity[p.OBJ_DEVICE_UID])!=16:raise ProtocolError('设备 UID 长度错误')
        with self.lock:
            self.identity=identity;self.boot_id=identity[p.OBJ_BOOT_ID];self.identified_at=time.monotonic();self.identified_utc=time.time()
        if self.wire[0]!=p.MAJOR:return self.latest_snapshot()
        self.request(p.OP_READ_STATE)
        deadline=time.monotonic()+1
        with self.condition:
            while not self.state_at and time.monotonic()<deadline:self.condition.wait(.02)
        if not self.state_at:raise ProtocolError('没有新鲜 STATUS')
        return self.latest_snapshot()
    def identify(self):
        with self.lock:return {'wireProtocol':self.wire,'objects':_json_objects(self.identity)}
    def capabilities(self):
        return self.latest_snapshot()['capabilities']
    def read_state(self):
        with self.lock:return {**self.state,'receivedAt':self.state_utc,'ageS':time.monotonic()-self.state_at}
    def subscribe_events(self,callback):
        previous=self.observe
        def observe(kind,payload):
            if previous:previous(kind,payload)
            callback(kind,payload)
        self.observe=observe
        return lambda:setattr(self,'observe',previous)

    def claim(self):
        if self.wire[0]!=p.MAJOR:raise ProtocolError('协议主版本不兼容，仅允许读取和 STOP')
        if self.identity.get(p.OBJ_DQ_CURRENT_CONVENTION)!=1:raise ProtocolError('dq 电流定义不兼容，保持只读')
        _,_,state=self.request(p.OP_READ_STATE)
        session=secrets.randbits(64) or 1;generation=state[p.OBJ_RUN_GENERATION]+1
        body=struct.pack('<QQII',self.boot_id,session,generation,1)
        self.request(p.OP_CLAIM_CONTROL,body)
        with self.lock:self.session_id=session;self.generation=generation;self.command_seq=1
    def prepare_run(self,config):
        if not self.session_id:self.claim()
        _,_,result=self.request(p.OP_PREPARE_RUN,objects(config),mutation=True)
        for oid,value in config.items():
            if result.get(oid)!=value:raise ProtocolError(f'运行配置读回不一致：{p.OBJECTS[oid][0]}')
        if not result.get(p.OBJ_PREPARED_ID):raise ProtocolError('缺少 prepared_id')
        _,_,limits=self.request(p.OP_READ_OBJECTS,struct.pack('<H',p.OBJ_PHASE_PROTECTION_CURRENT))
        with self.lock:self.identity.update(limits)
        return result[p.OBJ_PREPARED_ID]
    def start(self,prepared):
        threshold=time.monotonic()
        self.request(p.OP_START,objects({p.OBJ_PREPARED_ID:prepared}),mutation=True)
        with self.condition:
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if self.state_at>threshold and self.state.get('state')==2 and self.state.get('flags',0)&1 and self.sample_at>threshold and self.sample.get('flags',{}).get(2,0)&1 and self.sample.get('flags',{}).get(4,0)&1:return
                if self.state_at>threshold and self.state.get('state')==4:raise ProtocolError('设备启动触发保护')
                self.condition.wait(.02)
        raise TimeoutError('没有新鲜 RUNNING/output_on 确认')
    def set_target(self,current_a):return self.set_object_target(p.OBJ_IQ_TARGET,round(current_a*1000))
    def set_object_target(self,oid,value):return self.request(p.OP_SET_TARGET,objects({oid:value}),mutation=True)
    def stop(self):
        if not self.bus:raise OSError('CAN 未连接，不能确认电机停止')
        stop_id=secrets.randbits(32) or 1;threshold=time.monotonic()
        with self.condition:
            self.session_id=0
            for channel in self.channels:channel.reset()
            for item in self.pending.values():item['error']='控制已被停止取消'
            self.bus.stop(p.CAN_STOP+self.node,b'STOP'+struct.pack('<I',stop_id));self.condition.notify_all()
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if self.state_at>threshold and self.state.get('lastStopId')==stop_id and not self.state.get('flags',1)&1 and self.state.get('state') in (0,4):return {'lastStopId':stop_id,'deviceDisabled':True}
                self.condition.wait(.02)
        raise TimeoutError('STOP 未获新鲜输出关闭确认')
    def read_diagnostic(self,kind=2):return self.request(p.OP_READ_DIAGNOSTIC,struct.pack('<H',kind),channel=1)[2]
    def read_frozen(self):
        descriptor=self.read_diagnostic(3)[p.OBJ_FROZEN_TRANSFER_DESCRIPTOR]
        get=lambda n:descriptor[n][1]
        dataset,total,crc=get(1),get(5),get(6);data=bytearray()
        while len(data)<total:
            offset=len(data);body=tlv(1,p.TYPE_U32,dataset)+tlv(2,p.TYPE_U32,offset)+tlv(3,p.TYPE_U16,min(160,total-offset))
            _,_,part=self.request(p.OP_READ_TRANSFER,body,channel=1)
            if part[1]!=dataset or part[2]!=offset or zlib.crc32(part[3])!=part[4]:raise ProtocolError('冻结数据块校验失败')
            if not part[3]:raise ProtocolError('空数据块')
            data.extend(part[3])
        if len(data)!=total or zlib.crc32(data)!=crc:raise ProtocolError('冻结数据集不完整')
        return descriptor,bytes(data)
    def latest_snapshot(self):
        with self.lock:
            if self.identity and self.wire and self.wire[0]!=p.MAJOR:
                if self.error or time.monotonic()-self.identified_at>3:raise OSError(self.error or '只读身份反馈过期')
                return {'wireProtocol':list(self.wire),'deviceUid':self.identity[p.OBJ_DEVICE_UID].hex(),'bootId':str(self.boot_id),
                        'version':self.identity.get(p.OBJ_FIRMWARE_VERSION),'sdkVersion':self.identity.get(p.OBJ_SDK_VERSION),'node':self.node,
                        'capabilities':{'modes':0,'fields':0,'features':0,'maximumOutput':False},
                        'readiness':{'flags':0,'detail':'协议主版本不兼容，仅允许身份读取与 STOP'},
                        'motor':{'available':False,'output_on':None,'state':None,'updated_at':self.identified_utc},
                        'feedback':{'stale':True,'position_stale':True},'ntc':{'valid':False},'receivedAt':self.identified_utc}
            age=time.monotonic()-self.state_at
            if not self.identity or age>1.5 or self.error:raise OSError(self.error or 'MBP 状态反馈过期')
            f=self.sample.get('fields',{});flags=self.sample.get('flags',{});sample_age=time.monotonic()-self.sample_at
            valid=lambda n:n in f and flags.get(n,0)&1 and sample_age<=1.5
            signed=lambda x:x-(1<<32) if x&(1<<31) else x
            number=lambda n,scale=1000:signed(f[n])/scale if valid(n) else None
            position=(f.get(7,0)<<32)|f.get(6,0)
            if position&(1<<63):position-=1<<64
            position_valid=valid(6) and valid(7);ready=self.identity.get(p.OBJ_READINESS_FLAGS,0)
            iqmax=self.identity.get(p.OBJ_IQ_MAX);limit=self.identity.get(p.OBJ_PHASE_PROTECTION_CURRENT)
            mode=self.state['mode'];state=self.state['state'];sf=self.state['flags']
            capabilities={'modes':self.identity[p.OBJ_CONTROL_MODES],'fields':self.identity[p.OBJ_FEEDBACK_FIELDS],'features':self.identity.get(p.OBJ_OPTIONAL_FEATURES,0),'maximumOutput':bool(self.identity[p.OBJ_CONTROL_MODES]&(1<<5) and self.identity.get(p.OBJ_OPTIONAL_FEATURES,0)&1 and p.OBJ_AVAILABLE_IQ in self.identity and p.OBJ_ACTIVE_LIMITS in self.identity),'coherentSample':bool(self.identity.get(p.OBJ_OPTIONAL_FEATURES,0)&4)}
            result={'motorProfile':self.profile['motor'],'profileProvenance':self.profile['provenance'],'wireProtocol':list(self.wire),'sdkVersion':self.identity.get(p.OBJ_SDK_VERSION),'version':self.identity.get(p.OBJ_FIRMWARE_VERSION),'deviceUid':self.identity[p.OBJ_DEVICE_UID].hex(),'bootId':str(self.boot_id),'node':self.node,'capabilities':capabilities,'dynamicLimits':{'availableIqA':self.identity.get(p.OBJ_AVAILABLE_IQ,0)/1000,'activeLimits':self.identity.get(p.OBJ_ACTIVE_LIMITS)},'limits':_json_objects({k:v for k,v in self.identity.items() if 0x200<=k<0x300}),
              'readiness':{'flags':ready,'detail':self.identity.get(p.OBJ_READINESS_DETAIL),'faultCategory':self.identity.get(p.OBJ_FAULT_CATEGORY),'vendorCode':self.identity.get(p.OBJ_VENDOR_FAULT_CODE),'lastFaultEvent':self.last_fault_event},
              'motor':{'available':True,'mode':mode,'state':state,'state_name':['DISABLED','STARTING','RUNNING','STOPPING','FAULT'][state] if state<=4 else 'UNKNOWN','output_on':bool(sf&1),'fault':bool(sf&4),'fault_reason':self.identity.get(p.OBJ_VENDOR_FAULT_CODE),'updated_at':self.state_utc,'age_s':age},
              'phaseCurrent':{'ia_mA':number(16,1),'ib_mA':number(17,1),'ic_mA':number(18,1),'stale':not all(valid(n) for n in (16,17,18)),'updated_at':self.sample_utc},'encoder':{'valid':position_valid,'stale':not position_valid,'electrical_calibrated':bool(ready&4),'electrical_angle_valid':bool(ready&32)},
              'ntc':{'temp_c':number(10),'sourceDeviceTimeUs':str((f.get(22,0)<<32)|f.get(21,0)) if valid(21) and valid(22) else None,'valid':valid(10),'limit_c':self.identity.get(p.OBJ_MOTOR_TEMPERATURE_TRIP,0)/1000 or None,'over':bool(ready&8),'fault':not valid(10),'tripped_latched':bool(ready&16 and self.identity.get(p.OBJ_FAULT_CATEGORY)==3),'age_s':sample_age},
              'feedback':{'iq_a':number(2),'id_a':number(4),'iq_reference_a':number(3),'speed_rpm':number(5),'position_deg':position/1000000*360 if position_valid else None,'positionMicroturns':str(position) if position_valid else None,'deviceTimeUs':str((f.get(1,0)<<32)|f.get(0,0)),'sampleSeq':self.sample.get('seq'),'stale':not valid(2),'position_stale':not position_valid,'updated_at':self.sample_utc,'position_at':self.sample_utc},
              'control':{'mode':mode,'state':state,'iq_target_ma':signed(f[12]) if valid(12) else None,'updated_at':self.sample_utc},'receivedAt':self.sample_utc,'limitA':limit/1000 if limit else None,'supportedMaxA':iqmax/1000 if iqmax else None,'fullTargetA':min(10,iqmax/1000) if iqmax else None,'maximumOutput':self._maximum()}
            return result
    def _maximum(self):
        if not self.maximum:return None
        raw=self.maximum.get('window') or {};w={k:v[1] for k,v in raw.items()}
        return {'state':self.maximum['state'],'receivedAt':self.maximum['updated_at'],'updated_at':self.maximum['updated_at'],'schema':w.get(1),'windowId':w.get(2),'mcuTimeUs':w.get(3),'iqReferenceA':w.get(7,0)/1000,'idReferenceA':w.get(8,0)/1000,'operatingCurrentA':w.get(9,0)/1000,'voltageLimitV':self.identity.get(p.OBJ_DQ_VOLTAGE_LIMIT,0)/1000,'flags':w.get(6),'iqMeanA':w.get(10,0)/1000,'idMeanA':w.get(11,0)/1000,'raw':w}
    def close(self):
        self.closed.set()
        with self.condition:
            for item in self.pending.values():item['error']='CAN connection closed'
            self.condition.notify_all()
        if self.thread:self.thread.join(1)
        if self.diagnostic_thread:self.diagnostic_thread.join(1)
        if self.bus:self.bus.close();self.bus=None

def _json_objects(objs):
    def convert(v):
        if isinstance(v,bytes):return v.hex()
        if isinstance(v,dict):return {str(k):convert(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [convert(x) for x in v]
        if isinstance(v,int) and abs(v)>9007199254740991:return str(v)
        return v
    return convert(objs)
