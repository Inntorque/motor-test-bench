#include "motor_bench_port.h"
#include "mb_sdk.h"
#include "foc_mode.h"
#include "foc_tick.h"
#include "foc_ntc.h"
#include "foc_adc.h"
#include "foc_encoder.h"
#include "foc_pwm.h"
#include "ota_params.h"
#include "ota_proto.h"
#include "ota_version.h"
#include "n32g45x.h"
#include <string.h>
#include <stdio.h>

static mb_sdk sdk;
static uint64_t boot_id,clock_high;
static uint32_t clock_previous;
static mb_run_config prepared;
static uint8_t initialized;
extern int ota_can_try_send_native(uint16_t,const uint8_t *);

static uint64_t clock_us(void *user)
{
    (void)user;uint32_t low=foc_tick_micros();
    if(low<clock_previous)clock_high+=UINT64_C(1)<<32;
    clock_previous=low;return clock_high|low;
}
static uint64_t boot(void *user){(void)user;return boot_id;}
static int send(void *user,const mb_can_frame *f,uint8_t priority)
{(void)user;(void)priority;return ota_can_try_send_native(f->id,f->data);}
static uint32_t readiness(void)
{
    uint8_t stat[7];foc_mode_stat(stat);
    uint8_t calibrated=ota_params_calibration_current();
    uint8_t feedback=(foc_encoder_status()&FOC_ENCODER_STATUS_VALID)&&foc_adc_alive()&&foc_adc_trig_mode()==1u;
    uint32_t flags=2u|(calibrated?4u:0u)|(foc_ntc_over_temp()?8u:0u)|((stat[1]==MOTOR_STATE_FAULT||foc_ntc_trips()!=0u)?16u:0u)|(feedback?32u:0u);
    if(calibrated&&feedback&&!foc_ntc_over_temp()&&stat[1]!=MOTOR_STATE_FAULT&&foc_ntc_trips()==0u&&!ota_proto_rx_active())flags|=1u;
    return flags;
}
static void state(void *user,mb_state *out)
{
    (void)user;uint8_t raw[7];foc_mode_stat(raw);uint32_t ready=readiness();
    out->state=raw[1]==MOTOR_STATE_FAULT?4:raw[1];
    out->mode=raw[0]==CONTROL_MODE_CURRENT?1:raw[0]==CONTROL_MODE_MAX_OUTPUT?5:raw[0]==CONTROL_MODE_VELOCITY?3:raw[0]==CONTROL_MODE_POSITION?4:0;
    out->flags=(foc_pwm_outputs_on()?1u:0u)|((ready&1u)?2u:8u)|((ready&16u)?4u:0u)|((ready&32u)?16u:0u)|(ota_proto_rx_active()?32u:0u);
}
static int descriptor(void *user,uint16_t id,uint8_t *out,uint16_t *length)
{
    (void)user;uint16_t cap=*length,at=0;uint64_t v;uint8_t type=MB_TYPE_U32;
    switch(id){
    case MB_OBJ_DEVICE_UID:{uint8_t uid[16]={0};memcpy(uid,(const void *)UID_BASE,UID_LENGTH);memcpy(uid+12,"ITQ1",4);int r=mb_tlv(out,&at,cap,id,MB_TYPE_BYTES,uid,16);*length=at;return r;}
    case MB_OBJ_HARDWARE_IDENTITY:{const char *name="Inntorque N32G455 / SG-6010HB";int r=mb_tlv(out,&at,cap,id,MB_TYPE_UTF8,name,(uint16_t)strlen(name));*length=at;return r;}
    case MB_OBJ_FIRMWARE_VERSION:{char text[12];int n=snprintf(text,sizeof text,"0x%04X",OTA_BUILD_VERSION);int r=mb_tlv(out,&at,cap,id,MB_TYPE_UTF8,text,(uint16_t)n);*length=at;return r;}
    case MB_OBJ_DIAGNOSTIC_DESCRIPTOR:{uint8_t nested[64];uint16_t off=0;
        mb_number(nested,&off,sizeof nested,1,MB_TYPE_U16,2);mb_number(nested,&off,sizeof nested,2,MB_TYPE_U16,1);
        mb_tlv(nested,&off,sizeof nested,3,MB_TYPE_UTF8,"N32 completed FOC tick",22);
        int r=mb_tlv(out,&at,cap,id,MB_TYPE_NESTED,nested,off);*length=at;return r;}
    case MB_OBJ_CONTROL_MODES:v=(1u<<1)|(1u<<3)|(1u<<4)|(1u<<5);break;
    case MB_OBJ_FEEDBACK_FIELDS:v=0xff74ffu;type=MB_TYPE_U64;break; /* no measured Vbus/Ibus on this board */
    case MB_OBJ_OPTIONAL_FEATURES:v=1u|4u|8u|16u;type=MB_TYPE_U64;break;
    case MB_OBJ_DQ_CURRENT_CONVENTION:v=1;type=MB_TYPE_U8;break;
    case MB_OBJ_IQ_MIN:v=(uint32_t)-FOC_CTRL_MAX_CURRENT_MA;type=MB_TYPE_I32;break;
    case MB_OBJ_IQ_MAX:v=FOC_CTRL_MAX_CURRENT_MA;type=MB_TYPE_I32;break;
    case MB_OBJ_PHASE_PROTECTION_CURRENT:v=(uint32_t)ota_params_get(7u);break;
    case MB_OBJ_PHASE_RUN_LIMIT_MAX:v=FOC_CTRL_MAX_CURRENT_MA;break;
    case MB_OBJ_ALLOWED_NEGATIVE_ID_MIN:v=0;type=MB_TYPE_I32;break;
    case MB_OBJ_DQ_VOLTAGE_LIMIT:v=(uint32_t)ota_params_get(12u);break;
    case MB_OBJ_ROTOR_SPEED_ABS_MAX:v=(uint32_t)ota_params_get(11u)*1000u;break;
    case MB_OBJ_MOTOR_TEMPERATURE_TRIP:v=FOC_NTC_TRIP_C_X10*100;type=MB_TYPE_I32;break;
    case MB_OBJ_DEVICE_WATCHDOG_MS:v=FOC_HOST_TIMEOUT_MS;break;
    case MB_OBJ_READINESS_FLAGS:v=readiness();break;
    case MB_OBJ_FAULT_CATEGORY:case MB_OBJ_VENDOR_FAULT_CODE:{uint16_t d[3];foc_mode_fault_diag(d);v=d[0];if(id==MB_OBJ_FAULT_CATEGORY){type=MB_TYPE_U16;v=v==0?0:v==1||v==11?4:v==2||v==4||v==5||v==7?2:v==3?3:v==6?5:v==10?7:255;}break;}
    case MB_OBJ_READINESS_DETAIL:{uint32_t r=readiness();const char *text=(r&1)?"ready":(r&16)?"device fault":!(r&4)?"electrical calibration required":(r&8)?"NTC restart inhibited":!(r&32)?"encoder/ADC feedback not ready":"maintenance active";int rc=mb_tlv(out,&at,cap,id,MB_TYPE_UTF8,text,(uint16_t)strlen(text));*length=at;return rc;}
    case MB_OBJ_MAXIMUM_STATE:{uint16_t w[27];foc_mode_max_diag(w);v=w[0]==1?2:foc_motor_running()?1:0;type=MB_TYPE_U8;break;}
    case MB_OBJ_AVAILABLE_IQ:case MB_OBJ_ACTIVE_LIMITS:{foc_bench_snapshot_t f;foc_mode_bench_snapshot(&f);v=id==MB_OBJ_AVAILABLE_IQ?(uint32_t)f.available_iq_ma:((f.limits&7u)|((f.limits&16u)>>1)|((f.limits&8u)<<1));type=id==MB_OBJ_AVAILABLE_IQ?MB_TYPE_I32:MB_TYPE_U32;break;}
    default:return MB_UNSUPPORTED;
    }
    int rc=mb_number(out,&at,cap,id,type,v);*length=at;return rc;
}
static int snapshot(void *user,mb_snapshot *out)
{
    (void)user;foc_bench_snapshot_t f;foc_mode_bench_snapshot(&f);uint64_t now=clock_us(0);
    memset(out,0,sizeof *out);uint8_t motor_state[7];foc_mode_stat(motor_state);
    uint8_t running=motor_state[1]==MOTOR_STATE_RUNNING;
    out->time_us=running?now-(uint32_t)((uint32_t)now-f.source_us):now;
    out->position=(f.position_count/16384)*1000000+(f.position_count%16384)*1000000/16384;
    out->fields=0xff74ffu;out->valid=0xc3u;
    if(f.sequence&&(uint32_t)((uint32_t)now-f.source_us)<1500000u && running)out->valid|=0x703cu;
    if(foc_ntc_valid())out->valid|=1u<<10;
    uint64_t temperature_time=now-(uint32_t)((uint32_t)now-foc_ntc_updated_us());
    out->value[21]=(int32_t)temperature_time;out->value[22]=(int32_t)(temperature_time>>32);out->valid|=3u<<21;
    if(!(foc_encoder_status()&FOC_ENCODER_STATUS_VALID))out->valid&=~0xe0u;
    if(running&&(uint32_t)((uint32_t)now-f.source_us)>2000u)out->warning|=0x9f703cu;
    out->value[2]=f.iq_ma;out->value[3]=f.reference_ma;out->value[4]=f.id_ma;out->value[5]=f.speed_mrpm;
    out->value[10]=foc_ntc_temp_c_x10()*100;out->value[12]=f.target_ma;
    out->value[13]=ota_params_get(12u);out->valid|=1u<<13;
    out->value[14]=f.estimated_bus_ma;out->estimated=1u<<14;
    for(unsigned i=0;i<3;i++)out->value[16+i]=f.phase_ma[i];
    out->value[19]=(int32_t)f.sequence;out->value[20]=(int32_t)f.pwm_generation;out->value[23]=f.encoder_raw;
    if(f.sequence&&running&&(uint32_t)((uint32_t)now-f.source_us)<1500000u)out->valid|=0x9f0000u;
    return MB_OK;
}
static int prepare(void *user,const mb_run_config *requested,mb_run_config *applied)
{
    (void)user;
    if(foc_motor_running()||ota_proto_rx_active())return MB_BUSY;
    if(!(readiness()&1))return MB_NOT_READY;
    if(requested->mode!=1&&requested->mode!=3&&requested->mode!=4&&requested->mode!=5)return MB_UNSUPPORTED;
    if(requested->direction!=1)return MB_UNSUPPORTED; /* existing maximum allocator is positive only */
    if(requested->negative_id_ma!=0)return MB_UNSUPPORTED;
    if(requested->mode!=5&&(requested->bus_ma||requested->bus_mv||requested->speed_mrpm))return MB_INVALID_ARGUMENT;
    if(requested->mode!=3&&requested->velocity_mrpm)return MB_INVALID_ARGUMENT;
    if(requested->mode!=4&&requested->position_microturns)return MB_INVALID_ARGUMENT;
    if(requested->phase_ma>FOC_CTRL_MAX_CURRENT_MA||requested->iq_ma>FOC_CTRL_MAX_CURRENT_MA||requested->iq_ma<-FOC_CTRL_MAX_CURRENT_MA)return MB_OUT_OF_RANGE;
    if(requested->mode==5&&(requested->bus_ma<100||requested->bus_ma>20000||requested->bus_mv<6000||requested->bus_mv>26400||requested->speed_mrpm>20000000||requested->speed_mrpm%1000))return MB_OUT_OF_RANGE;
    uint32_t limit=requested->phase_ma?requested->phase_ma:requested->mode==5?FOC_CTRL_MAX_CURRENT_MA:(uint32_t)ota_params_get(7u);
    if(limit<100||requested->iq_ma>(int32_t)limit||requested->iq_ma<-(int32_t)limit)return MB_OUT_OF_RANGE;
    if(ota_params_set(7u,(int32_t)limit)!=0||ota_params_get(7u)!=(int32_t)limit)return MB_DEVICE_FAULT;
    foc_mode_set_ocp((int32_t)limit);
    if(requested->mode==5){
        if(foc_mode_set_control_field(FOC_SET_MAX_BUS_MA,(int32_t)requested->bus_ma)||foc_mode_set_control_field(FOC_SET_MAX_BUS_MV,(int32_t)requested->bus_mv)||foc_mode_set_control_field(FOC_SET_MAX_CURRENT_MA,(int32_t)requested->phase_ma)||foc_mode_set_control_field(FOC_SET_MAX_ID_MIN_MA,0)||foc_mode_set_control_field(FOC_SET_MAX_SPEED_RPM,(int32_t)(requested->speed_mrpm/1000)))return MB_OUT_OF_RANGE;
    }
    if(foc_mode_set_control_field(FOC_SET_CURRENT_MA,requested->iq_ma))return MB_OUT_OF_RANGE;
    if(requested->mode==3 && foc_mode_set_control_field(FOC_SET_SPEED_MRPM,requested->velocity_mrpm))return MB_OUT_OF_RANGE;
    if(requested->mode==4){int64_t mdeg=requested->position_microturns/1000000*360000+requested->position_microturns%1000000*360000/1000000;
        if(mdeg>INT32_MAX||mdeg<INT32_MIN||foc_mode_set_control_field(FOC_SET_POSITION_MDEG,(int32_t)mdeg))return MB_OUT_OF_RANGE;}
    prepared=*requested;*applied=prepared;return MB_OK;
}
static int start(void *user,uint32_t id)
{
    (void)user;(void)id;
    if(ota_proto_rx_active()||!(readiness()&1))return MB_NOT_READY;
    foc_mode_host_seen(1);
    return foc_motor_ctrl(1,prepared.mode==5?CONTROL_MODE_MAX_OUTPUT:prepared.mode==3?CONTROL_MODE_VELOCITY:prepared.mode==4?CONTROL_MODE_POSITION:CONTROL_MODE_CURRENT,0,0,0)==0?MB_OK:MB_NOT_READY;
}
static int target(void *user,uint16_t id,int64_t value)
{
    (void)user;
    if(id==MB_OBJ_POSITION_TARGET){
        int64_t mdeg=value/1000000*360000+value%1000000*360000/1000000;
        if(mdeg>INT32_MAX||mdeg<INT32_MIN)return MB_OUT_OF_RANGE;
        return foc_mode_set_control_field(FOC_SET_POSITION_MDEG,(int32_t)mdeg)?MB_OUT_OF_RANGE:MB_OK;
    }
    if(value>INT32_MAX||value<INT32_MIN)return MB_OUT_OF_RANGE;
    if(id==MB_OBJ_VELOCITY_TARGET)return foc_mode_set_control_field(FOC_SET_SPEED_MRPM,(int32_t)value)?MB_OUT_OF_RANGE:MB_OK;
    int32_t limit=ota_params_get(7u);if(id!=MB_OBJ_IQ_TARGET||value>limit||value<-limit)return MB_OUT_OF_RANGE;
    return foc_mode_set_control_field(FOC_SET_CURRENT_MA,(int32_t)value)?MB_OUT_OF_RANGE:MB_OK;
}
static void stop(void *user,uint16_t reason){(void)user;(void)reason;foc_motor_ctrl(0,0,0,0,0);}
static void heartbeat(void *user){(void)user;foc_mode_host_seen(1);}
static uint32_t crc32(const uint8_t *data,unsigned size)
{uint32_t c=~0u;for(unsigned i=0;i<size;i++){c^=data[i];for(unsigned j=0;j<8;j++)c=(c>>1)^((0u-(c&1u))&0xedb88320u);}return ~c;}
static uint32_t dataset_id(void){uint16_t w[27];foc_mode_max_diag(w);return ((uint32_t)w[26]<<16)^w[3]^((uint32_t)w[4]<<1);}
static int diagnostic(void *user,uint16_t kind,uint8_t *out,uint16_t *length)
{
    (void)user;uint16_t cap=*length,at=0;uint8_t nested[232];uint16_t n=0;
    if(kind==1){
        uint16_t w[27];foc_mode_max_diag(w);
        if(w[0]!=1){mb_number(out,&at,cap,MB_OBJ_MAXIMUM_STATE,MB_TYPE_U8,foc_motor_running()?1:0);*length=at;return MB_OK;}
        const uint8_t types[]={2,3,4,3,3,3,5,5,3,5,5,3,3,3,2,5,2,5,3,2,2,2,2,2,3,3};
        uint64_t vals[]={1,w[26],(uint64_t)w[3]|(uint64_t)w[4]<<16,200000,w[2],(w[1]&~0x18u)|((w[1]&16u)>>1)|((w[1]&8u)<<1),(uint32_t)(int32_t)(int16_t)w[6],(uint32_t)(int32_t)(int16_t)w[5],w[7],(uint32_t)(int32_t)(int16_t)w[10],(uint32_t)(int32_t)(int16_t)w[11],w[12],w[13],w[14],w[15],(uint32_t)(int32_t)(int16_t)w[25],w[24],(uint32_t)(int32_t)(int16_t)w[18],(uint32_t)w[19]|(uint32_t)w[20]<<16,w[21],w[22],w[23],w[16],w[17],w[8],w[9]};
        for(unsigned i=0;i<26;i++)if(mb_number(nested,&n,sizeof nested,(uint16_t)(i+1),types[i],vals[i]))return MB_OUT_OF_RANGE;
        mb_number(out,&at,cap,MB_OBJ_MAXIMUM_STATE,MB_TYPE_U8,2);
        if(mb_tlv(out,&at,cap,MB_OBJ_MAXIMUM_WINDOW,MB_TYPE_NESTED,nested,n))return MB_OUT_OF_RANGE;
    }else if(kind==2){
        uint16_t words[39];uint8_t bytes[78];foc_mode_loop_diag_coherent(words);for(unsigned i=0;i<39;i++)mb_put16(bytes+2*i,words[i]);
        mb_number(nested,&n,sizeof nested,1,MB_TYPE_U16,2);mb_number(nested,&n,sizeof nested,2,MB_TYPE_U16,1);
        mb_tlv(nested,&n,sizeof nested,3,MB_TYPE_UTF8,"completed FOC tick",18);mb_tlv(nested,&n,sizeof nested,4,MB_TYPE_BYTES,bytes,78);
        mb_tlv(out,&at,cap,MB_OBJ_DIAGNOSTIC_DESCRIPTOR,MB_TYPE_NESTED,nested,n);
    }else if(kind==3){
        if(foc_motor_running())return MB_BUSY;
        uint8_t point[40];uint32_t c=~0u;unsigned count=foc_mode_max_trace_count();
        for(unsigned i=0;i<count;i++){if(foc_mode_max_trace_read((uint16_t)i,point))return MB_BUSY;for(unsigned k=0;k<40;k++){c^=point[k];for(unsigned j=0;j<8;j++)c=(c>>1)^((0u-(c&1u))&0xedb88320u);}}
        uint32_t vals[]={dataset_id(),1,count,40,count*40,~c,500};uint8_t types[]={3,2,3,2,3,3,3};
        for(unsigned i=0;i<7;i++)mb_number(nested,&n,sizeof nested,(uint16_t)(i+1),types[i],vals[i]);
        mb_tlv(out,&at,cap,MB_OBJ_FROZEN_TRANSFER_DESCRIPTOR,MB_TYPE_NESTED,nested,n);
    }else return MB_UNSUPPORTED;
    *length=at;return MB_OK;
}
static int frozen(void *user,const uint8_t *body,uint16_t size,uint8_t *out,uint16_t *length)
{
    (void)user;if(foc_motor_running())return MB_BUSY;
    if(size!=25||mb_get16(body)!=1||body[2]!=3||mb_get16(body+3)!=4||mb_get16(body+9)!=2||body[11]!=3||mb_get16(body+12)!=4||mb_get16(body+18)!=3||body[20]!=2||mb_get16(body+21)!=2)return MB_INVALID_ARGUMENT;
    uint32_t id=mb_get32(body+5),offset=mb_get32(body+14);uint16_t count=mb_get16(body+23),at=0,cap=*length;
    if(id!=dataset_id()||count>160||offset>foc_mode_max_trace_count()*40u||count>foc_mode_max_trace_count()*40u-offset)return MB_OUT_OF_RANGE;
    uint8_t payload[160],point[40];for(unsigned i=0;i<count;i++){unsigned pos=offset+i;if(i==0||pos%40==0){if(foc_mode_max_trace_read((uint16_t)(pos/40),point))return MB_BUSY;}payload[i]=point[pos%40];}
    mb_number(out,&at,cap,1,MB_TYPE_U32,id);mb_number(out,&at,cap,2,MB_TYPE_U32,offset);mb_tlv(out,&at,cap,3,MB_TYPE_BYTES,payload,count);mb_number(out,&at,cap,4,MB_TYPE_U32,crc32(payload,count));*length=at;return MB_OK;
}
void motor_bench_init(void)
{
    /* UID plus ADC/DWT startup variation: identity nonce, not authentication. */
    boot_id=UINT64_C(1469598103934665603);for(unsigned i=0;i<96;i++)boot_id=(boot_id^((uint64_t)DWT->CYCCNT<<16)^foc_adc_raw(i%3))*UINT64_C(1099511628211);
    boot_id^=*(const uint32_t *)UID_BASE;if(!boot_id)boot_id=1;
    mb_config config={MB_SDK_API,1,FOC_HOST_TIMEOUT_MS};
    mb_port port={0};port.can_try_send=send;port.clock_us=clock_us;port.boot_identity=boot;port.read_descriptor=descriptor;port.read_state=state;port.read_snapshot=snapshot;port.prepare_run=prepare;port.request_start=start;port.set_target=target;port.request_disable=stop;port.heartbeat=heartbeat;port.read_diagnostic=diagnostic;port.read_frozen_chunk=frozen;
    initialized=mb_sdk_init(&sdk,&config,&port)==MB_OK;
}
int motor_bench_rx_isr(uint16_t id,const uint8_t *data)
{
    if(id!=0x081&&id!=0x101&&id!=0x401&&id!=0x481)return 0;
    if(initialized){mb_can_frame f;f.id=id;memcpy(f.data,data,8);mb_sdk_on_can_rx_isr(&sdk,&f);}return 1;
}
void motor_bench_legacy_stop(void)
{
    mb_can_frame f={0};f.id=0x081u;memcpy(f.data,"STOP",4);mb_put32(f.data+4,sdk.status.last_stop_id+1u);
    if(initialized)mb_sdk_on_can_rx_isr(&sdk,&f);
}
void motor_bench_poll(void)
{
    static uint16_t reported_fault;
    if(!initialized)return;
    uint8_t raw[7];uint16_t fault[3];foc_mode_stat(raw);foc_mode_fault_diag(fault);
    if(raw[1]==MOTOR_STATE_FAULT&&fault[0]&&reported_fault!=fault[0]){
        if(mb_sdk_report_event(&sdk,fault[0],2,1)==0)reported_fault=fault[0];
    }else if(raw[1]!=MOTOR_STATE_FAULT)reported_fault=0;
    mb_sdk_poll(&sdk,clock_us(0));
}
