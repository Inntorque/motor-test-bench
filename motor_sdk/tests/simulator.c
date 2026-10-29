#include "mb_sdk.h"
#include <stdlib.h>
#include <string.h>
typedef struct {
    mb_sdk sdk;uint64_t now;mb_state state;mb_run_config run;
    mb_can_frame sent[4096];unsigned head,tail,starts,stops,targets;int32_t target;uint32_t protection;
    uint64_t boot;int64_t position;uint8_t maximum,motion_set;int32_t speed;
} simulator;
static int send(void *u,const mb_can_frame *f,uint8_t priority){(void)priority;simulator *s=u;if(s->head-s->tail>=4096)return -1;s->sent[s->head++%4096]=*f;return 0;}
static uint64_t clock_us(void *u){return ((simulator*)u)->now;}
static uint64_t boot(void *u){return ((simulator*)u)->boot;}
static void state(void *u,mb_state *out){*out=((simulator*)u)->state;}
static int descriptor(void *u,uint16_t id,uint8_t *b,uint16_t *len){
    simulator *s=u;uint16_t at=0;uint8_t type=MB_TYPE_U32;uint64_t v;
    switch(id){
    case 1:{uint8_t uid[16]={1,2,3};mb_tlv(b,&at,*len,id,MB_TYPE_BYTES,uid,16);*len=at;return 0;}
    case 2:case 3:{const char *text=id==2?"portable simulator":"sim-1";mb_tlv(b,&at,*len,id,MB_TYPE_UTF8,text,(uint16_t)strlen(text));*len=at;return 0;}
    case MB_OBJ_CONTROL_MODES:v=(1u<<1)|(1u<<3)|(1u<<4)|(s->maximum?(1u<<5):0);break;
    case MB_OBJ_FEEDBACK_FIELDS:v=0x74ff;type=MB_TYPE_U64;break;
    case MB_OBJ_OPTIONAL_FEATURES:v=s->maximum?13:4;type=MB_TYPE_U64;break;
    case MB_OBJ_DQ_CURRENT_CONVENTION:v=1;type=MB_TYPE_U8;break;
    case MB_OBJ_IQ_MIN:v=(uint32_t)-30000;type=MB_TYPE_I32;break;
    case MB_OBJ_IQ_MAX:case MB_OBJ_PHASE_RUN_LIMIT_MAX:v=30000;type=id==MB_OBJ_IQ_MAX?MB_TYPE_I32:MB_TYPE_U32;break;
    case MB_OBJ_PHASE_PROTECTION_CURRENT:v=s->protection;break;
    case MB_OBJ_ALLOWED_NEGATIVE_ID_MIN:v=0;type=MB_TYPE_I32;break;
    case MB_OBJ_DQ_VOLTAGE_LIMIT:v=9000;break;
    case MB_OBJ_ROTOR_SPEED_ABS_MAX:v=2000000;break;
    case MB_OBJ_MOTOR_TEMPERATURE_TRIP:v=80000;type=MB_TYPE_I32;break;
    case MB_OBJ_DEVICE_WATCHDOG_MS:v=1000;break;
    case MB_OBJ_AVAILABLE_IQ:v=(uint32_t)s->target;type=MB_TYPE_I32;break;
    case MB_OBJ_ACTIVE_LIMITS:v=1;break;
    case MB_OBJ_READINESS_FLAGS:v=39;break;
    case MB_OBJ_FAULT_CATEGORY:v=0;type=MB_TYPE_U16;break;
    case MB_OBJ_VENDOR_FAULT_CODE:v=0;break;
    case MB_OBJ_READINESS_DETAIL:mb_tlv(b,&at,*len,id,MB_TYPE_UTF8,"ready",5);*len=at;return 0;
    default:return MB_UNSUPPORTED;
    }
    int rc=mb_number(b,&at,*len,id,type,v);*len=at;return rc;
}
static int snapshot(void *u,mb_snapshot *out){
    simulator *s=u;memset(out,0,sizeof *out);out->fields=out->valid=0x74ff;out->time_us=s->now;out->position=s->position;
    out->value[2]=s->target;out->value[3]=s->target;out->value[4]=-321;out->value[5]=s->state.state==2?(s->motion_set?s->speed:50000):0;out->value[10]=25000;out->value[12]=s->target;out->value[13]=9000;out->value[14]=1234;out->estimated=1u<<14;return 0;
}
static int prepare(void *u,const mb_run_config *r,mb_run_config *a){simulator *s=u;if(r->mode==5&&!s->maximum)return MB_UNSUPPORTED;if(r->mode!=1&&r->mode!=3&&r->mode!=4&&r->mode!=5)return MB_UNSUPPORTED;if(r->negative_id_ma)return MB_UNSUPPORTED;*a=*r;s->run=*r;if(r->phase_ma)s->protection=r->phase_ma;else if(r->mode==5)s->protection=30000;return 0;}
static int start(void *u,uint32_t id){(void)id;simulator *s=u;s->starts++;s->state.state=2;s->state.mode=s->run.mode;s->state.flags|=1;s->target=s->run.mode==5?24000:s->run.iq_ma;return 0;}
static int target(void *u,uint16_t id,int64_t v){simulator *s=u;(void)id;if(v>30000||v<-30000)return MB_OUT_OF_RANGE;s->target=(int32_t)v;s->targets++;return 0;}
static void stop(void *u,uint16_t reason){simulator *s=u;(void)reason;s->stops++;s->state.state=0;s->state.flags&=~1u;s->target=0;}
void *sim_create(void){simulator *s=calloc(1,sizeof *s);s->boot=UINT64_C(0x1122334455667788);s->position=INT64_C(1234567890123);s->maximum=1;s->protection=5000;s->state.flags=18;
 mb_config c={MB_SDK_API,1,1000};mb_port p={0};p.user=s;p.can_try_send=send;p.clock_us=clock_us;p.boot_identity=boot;p.read_descriptor=descriptor;p.read_state=state;p.read_snapshot=snapshot;p.prepare_run=prepare;p.request_start=start;p.set_target=target;p.request_disable=stop;
 if(mb_sdk_init(&s->sdk,&c,&p)){free(s);return 0;}return s;}
void sim_destroy(void *u){free(u);}
void sim_step(void *u,uint64_t now){simulator *s=u;if(s->motion_set&&s->state.state==2)s->position+=(int64_t)(now-s->now)/1000*s->speed/60;s->now=now;mb_sdk_poll(&s->sdk,now);}
void sim_receive(void *u,uint16_t id,const uint8_t *data){simulator *s=u;mb_can_frame f;f.id=id;memcpy(f.data,data,8);mb_sdk_on_can_rx_isr(&s->sdk,&f);}
int sim_pop(void *u,uint16_t *id,uint8_t *data){simulator *s=u;if(s->head==s->tail)return 0;mb_can_frame *f=&s->sent[s->tail++%4096];*id=f->id;memcpy(data,f->data,8);return 1;}
unsigned sim_metric(void *u,unsigned key){simulator *s=u;switch(key){case 0:return sizeof(mb_sdk);case 1:return s->starts;case 2:return s->stops;case 3:return s->targets;case 4:return s->sdk.status.protocol_errors;case 5:return s->state.flags&1;case 6:return s->sdk.status.rx_dropped;default:return 0;}}
void sim_maximum(void *u,unsigned enabled){((simulator*)u)->maximum=(uint8_t)enabled;}

void sim_motion(void *u,int32_t speed){simulator *s=u;s->motion_set=1;s->speed=speed;}
