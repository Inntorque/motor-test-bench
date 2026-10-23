"""Read-only mode-7 diagnostics. All transactions yield to control/stop traffic."""
import csv
import json
import os
import struct
import time

MIN_VERSION = 0x0A3C
LIMITS = {1:'current', 2:'voltage', 4:'source_estimate', 8:'speed', 16:'adc_range'}
QUALITY = {0x100:'magnetic_diagnostic_missing', 0x200:'magnetic_diagnostic_flags',
           0x400:'sampling_window_unverified', 0x800:'adc_generation_gap'}

def signed(v):
    return v-65536 if v&32768 else v

def decode(words):
    if len(words)!=27 or words[0]!=1:
        raise ValueError('maximum-output diagnostic schema unavailable')
    w=words; flags=w[1]
    result=dict(schema=1, flags=flags, limits=[v for k,v in LIMITS.items() if flags&k],
        quality=[v for k,v in QUALITY.items() if flags&k], samples=w[2], windowMs=w[2]/10,
        mcuTimeUs=w[3]|w[4]<<16, idReferenceA=signed(w[5])/1000,
        iqReferenceA=signed(w[6])/1000, operatingCurrentA=w[7]/1000,
        busLimitA=w[8]/1000, voltageLimitV=w[9]/1000,
        iqMeanA=signed(w[10])/1000, idMeanA=signed(w[11])/1000,
        iqErrorRmsA=w[12]/1000, idErrorRmsA=w[13]/1000,
        peakPhaseA=w[14]/1000, saturatedFraction=w[15]/1000,
        launchCnt=w[16], windowCcrA=w[17], sampleMarginTicks=signed(w[18]),
        pwmGeneration=w[19]|w[20]<<16, magneticWord=w[21], magneticMagnitude=w[22],
        magneticReadErrors=w[23], minAdcRaw=w[24], estimatedBusA=w[25]/1000,
        windowId=w[26], receivedAt=time.time(),
        boundaryMeaning='configured observable operating boundary; physical rating/calibration not certified')

    tolerance=max(.3,.05*max(abs(result['iqReferenceA']),abs(result['idReferenceA'])))
    tracking=(result['iqErrorRmsA']<=tolerance and result['idErrorRmsA']<=tolerance
              and abs(result['iqMeanA']-result['iqReferenceA'])<=tolerance)
    result.update(trackingToleranceA=tolerance,trackingWithinTolerance=tracking,
        configuredBoundaryObserved=bool(flags&0x0f) and tracking and not result['quality'])
    return result


def _request(link, op, a=0, b=0):
    reply=link.request('MAX_OUTPUT',link.param_host_id,link.param_dev_id,
        bytes([0x0D,op,a,b,0]),(0x91,0x82),attempts=1,priority=2)
    if not reply or len(reply)!=8 or reply[0]!=0x91:
        raise ValueError('maximum-output diagnostic read rejected or timed out')
    return reply

def status(link):
    if (link.info.get('info',{}).get('version') or 0)<MIN_VERSION:
        raise ValueError('最大输出模式需要 0x0A3C 或更新固件')
    with link.max_output_lock:
        cached=getattr(link,'max_output_snapshot',None)
        if cached and time.time()-cached['receivedAt']<.75:
            return dict(cached)
        generation=link.max_run_generation
        words=[]
        for bank in range(39,48):
            reply=_request(link,4,bank)
            if reply[1]!=bank:raise ValueError('maximum-output diagnostic bank mismatch')
            words.extend(struct.unpack('<3H',reply[2:]))
        if generation!=link.max_run_generation:raise ValueError('新试验已开始，丢弃旧诊断窗口')
        decoded=decode(words)
        with link.state_lock:link.max_output_snapshot=decoded
        return dict(decoded)

def monitor(link, generation):
    """Single passive publisher; diagnostics never hold a control HTTP request."""
    while not link._stop and generation==link.max_run_generation:
        motor=link.motor_status_snapshot()
        if motor.get('state') not in (1,2):break
        try:
            status(link)
            link.max_output_error=None
        except ValueError as exc:
            link.max_output_error=str(exc)
        time.sleep(.25)


TRACE_COLUMNS=['mcu_time_us','adc_raw_a','adc_raw_b','adc_raw_c','encoder_raw',
    'id_a','iq_a','id_ref_a','iq_ref_a','vd_alloc_v','vq_alloc_v','adc_sequence',
    'launch_cnt','sample_margin_ticks','pwm_generation','flags','magnetic_word']

def trace_row(raw):
    w=struct.unpack('<20H',raw)
    return [w[0]|w[1]<<16,*w[2:6],*[signed(v)/1000 for v in w[6:12]],
            w[12]|w[13]<<16,w[14],signed(w[15]),w[16]|w[17]<<16,w[18],w[19]]

def export_trace(link, folder, generation):
    """Only frozen data. A new start cancels export; no state-changing command."""
    report={'schema':1,'rows':0,'complete':False,'sampleRateHz':2000,
            'note':'last 128 ms at most; MCU timestamps, not host polling frequency'}
    if not link.max_trace_lock.acquire(blocking=False):
        report['error']='上一轮轨迹导出尚未释放，未导出本轮轨迹'
        with open(os.path.join(folder,'maximum_trace_status.json'),'w',encoding='utf-8') as f:
            json.dump(report,f,ensure_ascii=False,indent=2)
        return
    try:
        if generation!=link.max_run_generation:return
        reply=_request(link,5)
        if reply[1]!=5 or reply[4]!=40:raise ValueError('trace schema mismatch')
        count=min(256,reply[2]|reply[3]<<8);report['expectedRows']=count
        with open(os.path.join(folder,'maximum_trace.csv'),'w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f);writer.writerow(TRACE_COLUMNS)
            for index in range(count):
                if link._stop or generation!=link.max_run_generation:
                    raise ValueError('新试验开始或服务关闭，旧轨迹导出停止')
                reply=_request(link,6,index&255,index>>8)
                if reply[1:4]!=bytes([6,index&255,index>>8]):raise ValueError('trace index mismatch')
                raw=b''
                for chunk in range(7):
                    if generation!=link.max_run_generation:raise ValueError('new run interrupted trace export')
                    reply=_request(link,7,chunk)
                    if reply[1]!=chunk:raise ValueError('trace chunk mismatch')
                    raw+=reply[2:]
                writer.writerow(trace_row(raw[:40]));f.flush();report['rows']+=1
        report['complete']=True
    except Exception as exc:report['error']=str(exc)
    finally:
        try:
            with open(os.path.join(folder,'maximum_trace_status.json'),'w',encoding='utf-8') as f:
                json.dump(report,f,ensure_ascii=False,indent=2)
        finally:
            link.max_trace_lock.release()
