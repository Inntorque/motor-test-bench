"""MBP/1 framing and typed objects, independent of USB/CAN hardware."""
import struct
from . import generated as p

class ProtocolError(ValueError):
    pass

FORMATS={p.TYPE_U8:'B',p.TYPE_U16:'H',p.TYPE_U32:'I',p.TYPE_U64:'Q',p.TYPE_I32:'i',p.TYPE_I64:'q',p.TYPE_BOOL:'B'}

def tlv(oid,typ,value):
    if typ in FORMATS:
        if typ==p.TYPE_BOOL and value not in (0,1):raise ProtocolError('invalid bool')
        value=struct.pack('<'+FORMATS[typ],value)
    elif typ==p.TYPE_UTF8:value=value.encode('utf-8')
    else:value=bytes(value)
    return struct.pack('<HBH',oid,typ,len(value))+value

def objects(values):
    return b''.join(tlv(oid,p.OBJECTS[oid][1],value) for oid,value in values.items())

def decode_tlvs(body):
    result={};at=0
    while at<len(body):
        if len(body)-at<5:raise ProtocolError('truncated TLV')
        oid,typ,length=struct.unpack_from('<HBH',body,at);at+=5
        if oid in result or at+length>len(body) or not 1<=typ<=10:raise ProtocolError('invalid TLV')
        raw=body[at:at+length];at+=length
        if typ in FORMATS:
            if len(raw)!=struct.calcsize(FORMATS[typ]):raise ProtocolError('integer size')
            value=struct.unpack('<'+FORMATS[typ],raw)[0]
            if typ==p.TYPE_BOOL and value not in (0,1):raise ProtocolError('invalid bool')
        elif typ==p.TYPE_UTF8:value=raw.decode('utf-8')
        elif typ==p.TYPE_NESTED:value=decode_tlvs(raw)
        else:value=raw
        result[oid]=(typ,value)
    return result

def values(body):return {oid:v for oid,(_,v) in decode_tlvs(body).items()}

def packet(op,request,body=b'',flags=0,major=p.MAJOR,minor=p.MINOR):
    if len(body)>1008:raise ProtocolError('PDU too large')
    return struct.pack('<4sBBBBIHH',b'MTBP',major,minor,op,flags,request,len(body),0)+body

def unpack(data):
    if len(data)<16:raise ProtocolError('short header')
    magic,major,minor,op,flags,rid,size,reserved=struct.unpack_from('<4sBBBBIHH',data)
    if magic!=b'MTBP' or flags&~3 or reserved or size!=len(data)-16:raise ProtocolError('invalid header')
    return major,minor,op,flags,rid,data[16:]

class IsoTp:
    """Bounded normal-address ISO-TP endpoint. poll() never waits for frames."""
    def __init__(self,send,deliver,maximum=1024):
        self.send,self.deliver,self.maximum=send,deliver,maximum
        self.rx=bytearray();self.rx_size=0;self.rx_sn=1;self.rx_block=0
        self.tx=b'';self.tx_at=0;self.tx_sn=1;self.tx_block=0
        self.tx_wait=False;self.tx_next=0;self.stmin=.001;self.deadline=0;self.rx_deadline=0
        self.errors=0;self.waits=0
    def reset(self):
        self.rx.clear();self.rx_size=0;self.tx=b'';self.tx_wait=False
    def start(self,data,now):
        if self.tx:raise ProtocolError('ISO-TP busy')
        if not 0<len(data)<=self.maximum:raise ProtocolError('ISO-TP size')
        if len(data)<=7:self.send(bytes([len(data)])+data+b'\0'*(7-len(data)));return
        self.send(bytes([0x10|(len(data)>>8),len(data)&255])+data[:6])
        self.tx=bytes(data);self.tx_at=6;self.tx_sn=1;self.tx_wait=True;self.deadline=now+1;self.waits=0
    def receive(self,data,now):
        if len(data)!=8:self.errors+=1;return
        kind=data[0]>>4
        if kind==0:
            size=data[0]&15
            if not 1<=size<=7:self.errors+=1;return
            self.rx.clear();self.rx_size=0;self.deliver(data[1:1+size]);return
        if kind==1:
            size=((data[0]&15)<<8)|data[1]
            if not 8<=size<=self.maximum:self.errors+=1;self.send(b'\x32'+b'\0'*7);return
            self.rx=bytearray(data[2:]);self.rx_size=size;self.rx_sn=1;self.rx_block=0;self.rx_deadline=now+1
            self.send(b'\x30\x08\x01'+b'\0'*5);return
        if kind==2:
            if not self.rx_size or (data[0]&15)!=self.rx_sn:
                self.rx_size=0;self.rx.clear();self.errors+=1;return
            self.rx.extend(data[1:1+min(7,self.rx_size-len(self.rx))]);self.rx_sn=(self.rx_sn+1)&15
            self.rx_deadline=now+1;self.rx_block+=1
            if len(self.rx)==self.rx_size:
                result=bytes(self.rx);self.rx_size=0;self.rx.clear();self.deliver(result)
            elif self.rx_block==8:
                self.rx_block=0;self.send(b'\x30\x08\x01'+b'\0'*5)
        elif kind==3 and self.tx:
            flow=data[0]&15
            if flow==0:
                gap=data[2]
                if gap>127 and not 0xf1<=gap<=0xf9:self.reset();self.errors+=1;return
                self.stmin=gap/1000 if gap<=127 else (gap-0xf0)/10000
                self.tx_block=data[1] or 65535;self.tx_wait=False;self.tx_next=now;self.deadline=now+1
            elif flow==1 and self.waits<3:self.waits+=1;self.deadline=now+1
            else:self.tx=b'';self.errors+=1
    def poll(self,now):
        if self.rx_size and now>self.rx_deadline:self.rx.clear();self.rx_size=0;self.errors+=1
        if self.tx and now>self.deadline:self.tx=b'';self.errors+=1
        if not self.tx or self.tx_wait or now<self.tx_next:return
        chunk=self.tx[self.tx_at:self.tx_at+7]
        self.send(bytes([0x20|self.tx_sn])+chunk+b'\0'*(7-len(chunk)))
        self.tx_sn=(self.tx_sn+1)&15;self.tx_at+=len(chunk);self.tx_block-=1
        self.tx_next=now+max(self.stmin,.0001);self.deadline=now+1
        if self.tx_at==len(self.tx):self.tx=b''
        elif not self.tx_block:self.tx_wait=True

class SampleAssembler:
    def __init__(self):
        self.seq=None;self.fields={};self.flags={};self.dropped=0;self.duplicates=0;self.last=None
    def receive(self,data):
        if len(data)!=8:raise ProtocolError('sample DLC')
        seq,field,flags,value=struct.unpack('<HBBI',data)
        if flags&~7:raise ProtocolError('sample flags')
        if self.seq!=seq:
            if self.fields:self.dropped+=1
            self.seq=seq;self.fields={};self.flags={}
        if field==254:
            if self.last==seq:self.duplicates+=1;return None
            mask=sum(1<<i for i in self.fields)
            if value!=mask or value&3!=3:self.dropped+=1;self.fields={};return None
            if bool(value&64)!=bool(value&128):self.dropped+=1;self.fields={};return None
            fields=dict(self.fields);quality=dict(self.flags);self.fields={};self.last=seq
            return {'seq':seq,'fields':fields,'flags':quality}
        if field>=32:raise ProtocolError('sample field')
        if field in self.fields:self.duplicates+=1;self.fields={};self.seq=None;return None
        self.fields[field]=value;self.flags[field]=flags
        return None
