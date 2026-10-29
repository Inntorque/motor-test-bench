#include "mb_sdk.h"
#include <string.h>

uint16_t mb_get16(const uint8_t *p){return (uint16_t)(p[0]|(uint16_t)p[1]<<8);}
uint32_t mb_get32(const uint8_t *p){return (uint32_t)mb_get16(p)|(uint32_t)mb_get16(p+2)<<16;}
uint64_t mb_get64(const uint8_t *p){return (uint64_t)mb_get32(p)|(uint64_t)mb_get32(p+4)<<32;}
void mb_put16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
void mb_put32(uint8_t *p,uint32_t v){mb_put16(p,(uint16_t)v);mb_put16(p+2,(uint16_t)(v>>16));}
void mb_put64(uint8_t *p,uint64_t v){mb_put32(p,(uint32_t)v);mb_put32(p+4,(uint32_t)(v>>32));}
int mb_tlv(uint8_t *p,uint16_t *at,uint16_t cap,uint16_t id,uint8_t type,const void *data,uint16_t len)
{
    if((uint32_t)*at+len+5u>cap)return MB_OUT_OF_RANGE;
    mb_put16(p+*at,id);p[*at+2]=type;mb_put16(p+*at+3,len);memcpy(p+*at+5,data,len);*at+=len+5u;return MB_OK;
}
int mb_number(uint8_t *p,uint16_t *at,uint16_t cap,uint16_t id,uint8_t type,uint64_t value)
{
    uint8_t b[8];uint16_t n;
    switch(type){case MB_TYPE_U8:case MB_TYPE_BOOL:n=1;break;case MB_TYPE_U16:n=2;break;
    case MB_TYPE_U32:case MB_TYPE_I32:n=4;break;case MB_TYPE_U64:case MB_TYPE_I64:n=8;break;default:return MB_INVALID_ARGUMENT;}
    mb_put64(b,value);return mb_tlv(p,at,cap,id,type,b,n);
}
static int send_frame(mb_sdk *s,uint16_t id,const uint8_t *d,uint8_t priority)
{mb_can_frame f;f.id=id+s->config.node;memcpy(f.data,d,8);return s->port.can_try_send(s->port.user,&f,priority);}
static uint16_t response_id(unsigned ch){return ch?MB_CAN_DIAGNOSTIC_RESPONSE:MB_CAN_RESPONSE;}
static void transport_start(mb_sdk *s,unsigned ch,uint16_t size,uint64_t now)
{
    mb_isotp *t=&s->channel[ch];t->tx_size=size;t->tx_at=0;t->tx_sn=1;t->tx_wait=0;t->waits=0;t->tx_next=now;t->tx_deadline=now+1000000u;
}
static void transport_poll(mb_sdk *s,unsigned ch,uint64_t now)
{
    mb_isotp *t=&s->channel[ch];uint8_t d[8]={0};
    if(t->rx_size && now>t->rx_deadline){t->rx_size=0;t->rx_flow=0;s->status.protocol_errors++;}
    if(t->tx_size && now>t->tx_deadline){t->tx_size=0;s->status.protocol_errors++;}
    if(t->rx_flow){d[0]=t->rx_flow==2?0x32:0x30;d[1]=8;d[2]=1;if(send_frame(s,response_id(ch),d,(uint8_t)(ch+1))==0)t->rx_flow=0;return;}
    if(!t->tx_size||t->tx_wait||now<t->tx_next)return;
    uint16_t n;
    if(t->tx_size<=7){d[0]=(uint8_t)t->tx_size;memcpy(d+1,t->tx,t->tx_size);n=t->tx_size;}
    else if(!t->tx_at){d[0]=0x10|(t->tx_size>>8);d[1]=(uint8_t)t->tx_size;memcpy(d+2,t->tx,6);n=6;}
    else {n=t->tx_size-t->tx_at;if(n>7)n=7;d[0]=0x20|t->tx_sn;memcpy(d+1,t->tx+t->tx_at,n);}
    if(send_frame(s,response_id(ch),d,(uint8_t)(ch+1))!=0)return;
    if(!t->tx_at && t->tx_size>7)t->tx_wait=1;
    else {t->tx_sn=(t->tx_sn+1)&15;if(t->tx_block && --t->tx_block==0)t->tx_wait=1;}
    t->tx_at+=n;t->tx_next=now+t->stmin;t->tx_deadline=now+1000000u;
    if(t->tx_at==t->tx_size)t->tx_size=0;
}
static int config_decode(const uint8_t *b,uint16_t n,mb_run_config *r,uint32_t *prepared)
{
    uint32_t seen=0;uint16_t at=0;*prepared=0;
    while(at<n){
        if(n-at<5)return MB_INVALID_ARGUMENT;
        uint16_t id=mb_get16(b+at),len=mb_get16(b+at+3);uint8_t typ=b[at+2];at+=5;
        if(id<MB_OBJ_RUN_MODE||id>MB_OBJ_POSITION_TARGET||len>n-at)return MB_INVALID_ARGUMENT;
        uint32_t bit=1u<<(id-MB_OBJ_RUN_MODE);if(seen&bit)return MB_INVALID_ARGUMENT;seen|=bit;
        uint8_t want=(id==MB_OBJ_POSITION_TARGET)?MB_TYPE_I64:(id==MB_OBJ_VELOCITY_TARGET)?MB_TYPE_I32:(id==MB_OBJ_RUN_MODE)?MB_TYPE_U8:((id==MB_OBJ_DIRECTION||id==MB_OBJ_IQ_TARGET||id==MB_OBJ_NEGATIVE_ID_MIN)?MB_TYPE_I32:MB_TYPE_U32);
        if(typ!=want||len!=(want==MB_TYPE_U8?1:want==MB_TYPE_I64?8:4))return MB_INVALID_ARGUMENT;
        int32_t v=(int32_t)(len==1?b[at]:mb_get32(b+at));
        switch(id){case MB_OBJ_RUN_MODE:r->mode=(uint8_t)v;break;case MB_OBJ_DIRECTION:r->direction=v;break;
        case MB_OBJ_IQ_TARGET:r->iq_ma=v;break;case MB_OBJ_PHASE_RUN_LIMIT:r->phase_ma=(uint32_t)v;break;
        case MB_OBJ_BUS_CURRENT_BUDGET:r->bus_ma=(uint32_t)v;break;case MB_OBJ_BUS_VOLTAGE_BASIS:r->bus_mv=(uint32_t)v;break;
        case MB_OBJ_NEGATIVE_ID_MIN:r->negative_id_ma=v;break;case MB_OBJ_SPEED_SOFT_LIMIT:r->speed_mrpm=(uint32_t)v;break;
        case MB_OBJ_PREPARED_ID:*prepared=(uint32_t)v;break;
        case MB_OBJ_VELOCITY_TARGET:r->velocity_mrpm=v;break;
        case MB_OBJ_POSITION_TARGET:r->position_microturns=(int64_t)mb_get64(b+at);break;default:break;}
        at+=len;
    }
    return MB_OK;
}
static void config_encode(const mb_run_config *r,uint8_t *b,uint16_t *at,uint16_t cap)
{
    mb_number(b,at,cap,MB_OBJ_RUN_MODE,MB_TYPE_U8,r->mode);
    mb_number(b,at,cap,MB_OBJ_DIRECTION,MB_TYPE_I32,(uint32_t)r->direction);
    mb_number(b,at,cap,MB_OBJ_IQ_TARGET,MB_TYPE_I32,(uint32_t)r->iq_ma);
    mb_number(b,at,cap,MB_OBJ_PHASE_RUN_LIMIT,MB_TYPE_U32,r->phase_ma);
    mb_number(b,at,cap,MB_OBJ_BUS_CURRENT_BUDGET,MB_TYPE_U32,r->bus_ma);
    mb_number(b,at,cap,MB_OBJ_BUS_VOLTAGE_BASIS,MB_TYPE_U32,r->bus_mv);
    mb_number(b,at,cap,MB_OBJ_NEGATIVE_ID_MIN,MB_TYPE_I32,(uint32_t)r->negative_id_ma);
    mb_number(b,at,cap,MB_OBJ_SPEED_SOFT_LIMIT,MB_TYPE_U32,r->speed_mrpm);
    if(r->mode==3)mb_number(b,at,cap,MB_OBJ_VELOCITY_TARGET,MB_TYPE_I32,(uint32_t)r->velocity_mrpm);
    if(r->mode==4)mb_number(b,at,cap,MB_OBJ_POSITION_TARGET,MB_TYPE_I64,(uint64_t)r->position_microturns);
}
static void disable(mb_sdk *s,uint16_t reason)
{
    s->port.request_disable(s->port.user,reason);s->status.claimed=0;s->status.prepared=0;s->status.session=0;
    s->status.generation++;s->status.command_seq=0;s->prepared_id=0;
}
static int session_check(mb_sdk *s,const uint8_t *b,uint16_t n,uint8_t claim)
{
    if(n<24)return MB_INVALID_ARGUMENT;
    if(mb_get64(b)!=s->status.boot||!mb_get64(b+8))return MB_STALE_SESSION;
    if(!claim && (!s->status.claimed||mb_get64(b+8)!=s->status.session))return MB_STALE_SESSION;
    uint32_t gen=mb_get32(b+16),seq=mb_get32(b+20);
    if(claim){if(gen<=s->status.generation||!seq)return MB_STALE_GENERATION;}
    else if(gen!=s->status.generation||seq<=s->status.command_seq)return MB_STALE_GENERATION;
    return MB_OK;
}
static void handle_message(mb_sdk *s,unsigned ch,uint64_t now)
{
    mb_isotp *t=&s->channel[ch];uint8_t *q=t->rx,*out=t->tx;uint16_t n=t->rx_size;
    t->rx_size=0;
    if(n<16||memcmp(q,"MTBP",4)||q[7]||mb_get16(q+14)||mb_get16(q+12)!=n-16){s->status.protocol_errors++;return;}
    uint8_t op=q[6],stage=1;uint32_t rid=mb_get32(q+8);uint16_t rc=MB_OK,at=20;
    uint8_t *b=q+16;uint16_t size=n-16;uint8_t mutation=op>=MB_OP_CLAIM_CONTROL&&op<=MB_OP_SET_STREAMS;
    mb_state state={0};s->port.read_state(s->port.user,&state);
    if(q[4]!=MB_MAJOR && op!=MB_OP_IDENTIFY){rc=MB_UNSUPPORTED;goto reply;}
    if((op==MB_OP_READ_DIAGNOSTIC||op==MB_OP_READ_TRANSFER)!=(ch==1)){rc=MB_UNSUPPORTED;goto reply;}
    if(mutation){
        if(size<24){rc=MB_INVALID_ARGUMENT;goto reply;}
        /* STOP revocation is checked before replaying a cached command result. */
        if(op!=MB_OP_CLAIM_CONTROL && (mb_get64(b+8)!=s->status.session||mb_get32(b+16)!=s->status.generation||!s->status.claimed)){rc=MB_STALE_SESSION;goto reply;}
        if(n>sizeof(s->dedup[0].request_bytes)){rc=MB_OUT_OF_RANGE;goto reply;}
        for(unsigned i=0;i<4;i++)if(s->dedup[i].request==rid&&s->dedup[i].request_size){
            mb_dedup *d=&s->dedup[i];if(d->request_size!=n||memcmp(d->request_bytes,q,n)){rc=MB_INVALID_ARGUMENT;goto reply;}
            memcpy(out,d->response_bytes,d->response_size);transport_start(s,ch,d->response_size,now);return;
        }
        rc=(uint16_t)session_check(s,b,size,op==MB_OP_CLAIM_CONTROL);if(rc)goto reply;
        if(atomic_load_explicit(&s->stop_serial,memory_order_acquire)!=s->handled_stop){rc=MB_STALE_GENERATION;goto reply;}
        if(op==MB_OP_CLAIM_CONTROL){
            if(state.flags&1u||s->status.claimed){rc=MB_BUSY;goto remember;}
            s->status.session=mb_get64(b+8);s->status.generation=mb_get32(b+16);s->status.claimed=1;s->last_heartbeat=now;
        }
        s->status.command_seq=mb_get32(b+20);
        b+=24;size-=24;
    }
    switch(op){
    case MB_OP_IDENTIFY:case MB_OP_READ_OBJECTS:{
        static const uint16_t core[]={1,2,3,4,5,0x100,0x101,0x102,0x200,0x201,0x202,0x203,0x204,0x205,0x206,0x207,0x208,0x209,0x300,0x301,0x302,0x303,0x3000,0x3001,0x3002,0x4000};
        if(size&1u){rc=MB_INVALID_ARGUMENT;break;}
        unsigned count=size?size/2u:sizeof(core)/sizeof(core[0]);
        for(unsigned i=0;i<count;i++){
            uint16_t id=size?mb_get16(b+2*i):core[i];
            if(id==MB_OBJ_BOOT_ID)rc=(uint16_t)mb_number(out,&at,t->capacity,id,MB_TYPE_U64,s->status.boot);
            else if(id==MB_OBJ_SDK_VERSION)rc=(uint16_t)mb_tlv(out,&at,t->capacity,id,MB_TYPE_UTF8,MB_SDK_VERSION,sizeof(MB_SDK_VERSION)-1u);
            else {uint16_t len=t->capacity-at;int r=s->port.read_descriptor(s->port.user,id,out+at,&len);if(r==MB_OK)at+=len;else if(size)rc=(uint16_t)r;}
            if(rc)break;
        }break;}
    case MB_OP_CLAIM_CONTROL:break;
    case MB_OP_RELEASE_CONTROL:if(size){rc=MB_INVALID_ARGUMENT;break;}disable(s,0);break;
    case MB_OP_PREPARE_RUN:{
        mb_run_config requested={0},applied={0};uint32_t unused;
        if(state.flags&1u){rc=MB_BUSY;break;}
        s->status.prepared=0;
        rc=(uint16_t)config_decode(b,size,&requested,&unused);if(rc)break;
        if(unused){rc=MB_INVALID_ARGUMENT;break;}
        rc=(uint16_t)s->port.prepare_run(s->port.user,&requested,&applied);
        if(rc==MB_OK){s->run=applied;s->status.prepared=1;s->prepared_id=rid;config_encode(&applied,out,&at,t->capacity);mb_number(out,&at,t->capacity,MB_OBJ_PREPARED_ID,MB_TYPE_U32,rid);}break;}
    case MB_OP_START:{
        mb_run_config unused={0};uint32_t prepared;
        rc=(uint16_t)config_decode(b,size,&unused,&prepared);if(rc)break;
        if(size!=9||!prepared||!s->status.prepared||prepared!=s->prepared_id){rc=MB_NOT_READY;break;}
        if(state.flags&1u){rc=MB_BUSY;break;}
        rc=(uint16_t)s->port.request_start(s->port.user,prepared);s->status.prepared=0;
        if(rc==MB_OK){rc=MB_PENDING;stage=0;}break;}
    case MB_OP_SET_TARGET:{
        mb_run_config target={0};uint32_t unused;
        rc=(uint16_t)config_decode(b,size,&target,&unused);if(rc)break;
        uint16_t id=size>=2?mb_get16(b):0;
        if(size==9&&id==MB_OBJ_IQ_TARGET&&s->run.mode==1)rc=(uint16_t)s->port.set_target(s->port.user,id,target.iq_ma);
        else if(size==9&&id==MB_OBJ_VELOCITY_TARGET&&s->run.mode==3)rc=(uint16_t)s->port.set_target(s->port.user,id,target.velocity_mrpm);
        else if(size==13&&id==MB_OBJ_POSITION_TARGET&&s->run.mode==4)rc=(uint16_t)s->port.set_target(s->port.user,id,target.position_microturns);
        else rc=MB_INVALID_ARGUMENT;
        break;}
    case MB_OP_CLEAR_FAULT:rc=s->port.clear_fault?(uint16_t)s->port.clear_fault(s->port.user):MB_UNSUPPORTED;break;
    case MB_OP_SET_STREAMS:
        /* First release offers the fixed 20 ms recipe; unknown recipes are rejected. */
        if(size){rc=MB_UNSUPPORTED;break;}
        {uint8_t nested[64];uint16_t off=0;mb_number(nested,&off,sizeof nested,1,MB_TYPE_U32,s->period_us);mb_number(nested,&off,sizeof nested,4,MB_TYPE_U32,s->period_us);mb_tlv(out,&at,t->capacity,MB_OBJ_STREAM_GROUPS,MB_TYPE_NESTED,nested,off);}break;
    case MB_OP_READ_STATE:
        if(size){rc=MB_INVALID_ARGUMENT;break;}
        mb_number(out,&at,t->capacity,MB_OBJ_STATE,MB_TYPE_U8,state.state);
        mb_number(out,&at,t->capacity,MB_OBJ_MODE,MB_TYPE_U8,state.mode);
        mb_number(out,&at,t->capacity,MB_OBJ_STATUS_FLAGS,MB_TYPE_U16,state.flags);
        mb_number(out,&at,t->capacity,MB_OBJ_SESSION_ID,MB_TYPE_U64,s->status.session);
        mb_number(out,&at,t->capacity,MB_OBJ_RUN_GENERATION,MB_TYPE_U32,s->status.generation);
        mb_number(out,&at,t->capacity,MB_OBJ_COMMAND_SEQ,MB_TYPE_U32,s->status.command_seq);
        mb_number(out,&at,t->capacity,MB_OBJ_LAST_STOP_ID,MB_TYPE_U32,s->status.last_stop_id);break;
    case MB_OP_READ_DIAGNOSTIC:case MB_OP_READ_TRANSFER:{
        uint16_t len=t->capacity-at;
        if(op==MB_OP_READ_DIAGNOSTIC)rc=(size==2&&s->port.read_diagnostic)?(uint16_t)s->port.read_diagnostic(s->port.user,mb_get16(b),out+at,&len):MB_UNSUPPORTED;
        else rc=s->port.read_frozen_chunk?(uint16_t)s->port.read_frozen_chunk(s->port.user,b,size,out+at,&len):MB_UNSUPPORTED;
        if(rc==MB_OK)at+=len;
        break;}
    default:rc=MB_UNSUPPORTED;break;
    }
remember:
reply:
    if(atomic_load_explicit(&s->stop_serial,memory_order_acquire)!=s->handled_stop && mutation){rc=MB_STALE_GENERATION;at=20;}
    if(rc>MB_PENDING)at=20;
    memcpy(out,"MTBP",4);out[4]=MB_MAJOR;out[5]=MB_MINOR;out[6]=op;out[7]=(rc>MB_PENDING)?3:1;
    mb_put32(out+8,rid);mb_put16(out+12,at-16);mb_put16(out+14,0);mb_put16(out+16,rc);out[18]=stage;out[19]=0;
    if(mutation&&n<=sizeof(s->dedup[0].request_bytes)&&at<=sizeof(s->dedup[0].response_bytes)){
        mb_dedup *d=&s->dedup[s->dedup_next++%4];d->request=rid;d->request_size=n;d->response_size=at;memcpy(d->request_bytes,q,n);memcpy(d->response_bytes,out,at);
    }
    transport_start(s,ch,at,now);
}
static void receive_transport(mb_sdk *s,unsigned ch,const uint8_t *d,uint64_t now)
{
    mb_isotp *t=&s->channel[ch];uint8_t kind=d[0]>>4;
    if(kind==3&&t->tx_size){
        if((d[0]&15)==0){uint8_t gap=d[2];if(gap>127&&(gap<0xf1||gap>0xf9)){t->tx_size=0;s->status.protocol_errors++;return;}
            t->stmin=gap<=127?(uint32_t)gap*1000u:(uint32_t)(gap-0xf0)*100u;t->tx_block=d[1]?d[1]:65535;t->tx_wait=0;t->tx_next=now;t->tx_deadline=now+1000000u;}
        else if((d[0]&15)==1&&t->waits++<3)t->tx_deadline=now+1000000u;
        else {t->tx_size=0;s->status.protocol_errors++;}return;
    }
    if(t->tx_size)return;
    if(kind==0){uint8_t len=d[0]&15;if(!len||len>7){s->status.protocol_errors++;return;}memcpy(t->rx,d+1,len);t->rx_size=len;handle_message(s,ch,now);}
    else if(kind==1){uint16_t len=((uint16_t)(d[0]&15)<<8)|d[1];if(len<8||len>t->capacity){t->rx_size=0;t->rx_flow=2;s->status.protocol_errors++;return;}
        t->rx_size=len;t->rx_at=6;t->rx_sn=1;t->rx_block=0;t->rx_flow=1;t->rx_deadline=now+1000000u;memcpy(t->rx,d+2,6);}
    else if(kind==2){
        if(!t->rx_size||(d[0]&15)!=t->rx_sn){t->rx_size=0;t->rx_flow=0;s->status.protocol_errors++;return;}
        uint16_t len=t->rx_size-t->rx_at;if(len>7)len=7;memcpy(t->rx+t->rx_at,d+1,len);t->rx_at+=len;t->rx_sn=(t->rx_sn+1)&15;t->rx_deadline=now+1000000u;
        if(t->rx_at==t->rx_size)handle_message(s,ch,now);
        else if(++t->rx_block==8){t->rx_block=0;t->rx_flow=1;}
    }
}
int mb_sdk_init(mb_sdk *s,const mb_config *c,const mb_port *p)
{
    if(!s||!c||!p||c->api!=MB_SDK_API||!c->node||c->node>63||!c->watchdog_ms||!p->can_try_send||!p->clock_us||!p->boot_identity||!p->read_descriptor||!p->read_state||!p->read_snapshot||!p->prepare_run||!p->request_start||!p->set_target||!p->request_disable)return MB_INVALID_ARGUMENT;
    memset(s,0,sizeof *s);s->config=*c;s->port=*p;s->status.boot=p->boot_identity(p->user);if(!s->status.boot)return MB_NOT_READY;
    atomic_init(&s->head,0);atomic_init(&s->tail,0);atomic_init(&s->stop_serial,0);atomic_init(&s->stop_id,0);
    s->channel[0].rx=s->control_rx;s->channel[0].tx=s->control_tx;s->channel[0].capacity=1024;
    s->channel[1].rx=s->diag_rx;s->channel[1].tx=s->diag_tx;s->channel[1].capacity=256;
    s->period_us=20000;return MB_OK;
}
void mb_sdk_on_can_rx_isr(mb_sdk *s,const mb_can_frame *f)
{
    if(f->id==MB_CAN_STOP+s->config.node&&!memcmp(f->data,"STOP",4)){
        atomic_store_explicit(&s->stop_id,mb_get32(f->data+4),memory_order_relaxed);
        atomic_fetch_add_explicit(&s->stop_serial,1,memory_order_release);return;
    }
    uint16_t id=f->id-s->config.node;
    if(id!=MB_CAN_REQUEST&&id!=MB_CAN_DIAGNOSTIC_REQUEST&&id!=MB_CAN_CONTROL_HEARTBEAT)return;
    unsigned head=atomic_load_explicit(&s->head,memory_order_relaxed),tail=atomic_load_explicit(&s->tail,memory_order_acquire);
    if(head-tail>=MB_RX_DEPTH){s->status.rx_dropped++;return;}
    s->queue[head%MB_RX_DEPTH]=*f;atomic_store_explicit(&s->head,head+1,memory_order_release);
}
void mb_sdk_publish_snapshot(mb_sdk *s,const mb_snapshot *snapshot)
{
    if(s->sample_pending||s->sample_end){s->status.sample_dropped++;return;}
    s->snapshot=*snapshot;s->snapshot.fields|=3u;s->snapshot.valid|=3u;
    s->snapshot.value[0]=(int32_t)snapshot->time_us;s->snapshot.value[1]=(int32_t)(snapshot->time_us>>32);
    s->snapshot.value[6]=(int32_t)snapshot->position;s->snapshot.value[7]=(int32_t)((uint64_t)snapshot->position>>32);
    s->sample_pending=s->snapshot.fields;s->sample_end=1;s->sample_seq++;
}
static void publish_status(mb_sdk *s,uint64_t now)
{
    uint8_t d[8];mb_state state={0};s->port.read_state(s->port.user,&state);d[0]=state.state;d[1]=state.mode;mb_put16(d+2,state.flags);mb_put32(d+4,s->status.last_stop_id);
    if(send_frame(s,MB_CAN_STATUS,d,0)==0)s->next_status=now+20000u;
}
void mb_sdk_poll(mb_sdk *s,uint64_t now)
{
    unsigned serial=atomic_load_explicit(&s->stop_serial,memory_order_acquire);
    if(serial!=s->handled_stop){
        disable(s,0);s->handled_stop=serial;s->status.last_stop_id=atomic_load_explicit(&s->stop_id,memory_order_relaxed);s->status.stops++;
        atomic_store_explicit(&s->tail,atomic_load_explicit(&s->head,memory_order_acquire),memory_order_release);
        for(unsigned i=0;i<2;i++){s->channel[i].rx_size=s->channel[i].tx_size=0;s->channel[i].rx_flow=0;}
        s->next_status=0;
    }
    if(s->status.claimed&&now-s->last_heartbeat>(uint64_t)s->config.watchdog_ms*1000u){disable(s,MB_TIMEOUT);s->next_status=0;}
    for(unsigned i=0;i<16;i++){
        unsigned tail=atomic_load_explicit(&s->tail,memory_order_relaxed),head=atomic_load_explicit(&s->head,memory_order_acquire);if(tail==head)break;
        mb_can_frame f=s->queue[tail%MB_RX_DEPTH];atomic_store_explicit(&s->tail,tail+1,memory_order_release);
        if(f.id==MB_CAN_CONTROL_HEARTBEAT+s->config.node){if(s->status.claimed&&mb_get64(f.data)==s->status.session){s->last_heartbeat=now;if(s->port.heartbeat)s->port.heartbeat(s->port.user);}}
        else receive_transport(s,f.id==MB_CAN_DIAGNOSTIC_REQUEST+s->config.node,f.data,now);
        if(atomic_load_explicit(&s->stop_serial,memory_order_acquire)!=s->handled_stop)return;
    }
    if(now>=s->next_status)publish_status(s,now);
    if(now>=s->next_device){uint8_t d[8];mb_put32(d,(uint32_t)s->status.boot);mb_put32(d+4,(uint32_t)(now/1000u));if(send_frame(s,MB_CAN_DEVICE_HEARTBEAT,d,1)==0)s->next_device=now+250000u;}
    transport_poll(s,0,now);
    if(now>=s->next_sample&&!s->sample_pending&&!s->sample_end){if(s->port.read_snapshot(s->port.user,&s->snapshot)==MB_OK)mb_sdk_publish_snapshot(s,&s->snapshot);s->next_sample=now+s->period_us;}
    /* Bounded burst leaves mailbox capacity for control/status. */
    for(unsigned k=0;k<2 && (s->sample_pending||s->sample_end);k++){
        uint8_t d[8];mb_put16(d,s->sample_seq);
        if(s->sample_pending){unsigned i=0;while(!(s->sample_pending&(1u<<i)))i++;
            d[2]=(uint8_t)i;d[3]=(s->snapshot.valid>>i&1u)|((s->snapshot.estimated>>i&1u)<<1)|((s->snapshot.warning>>i&1u)<<2);mb_put32(d+4,(uint32_t)s->snapshot.value[i]);
            if(send_frame(s,MB_CAN_SAMPLE,d,2)!=0)break;
            s->sample_pending&=~(1u<<i);
        }else{d[2]=254;d[3]=1;mb_put32(d+4,s->snapshot.fields);if(send_frame(s,MB_CAN_SAMPLE,d,2)!=0)break;s->sample_end=0;}
    }
    transport_poll(s,1,now);
}
int mb_sdk_report_event(mb_sdk *s,uint16_t code,uint8_t severity,uint8_t source)
{uint8_t d[8];mb_put16(d,code);d[2]=severity;d[3]=source;mb_put32(d+4,(uint32_t)(s->port.clock_us(s->port.user)/1000u));return send_frame(s,MB_CAN_EVENT,d,1);}
const mb_sdk_status *mb_sdk_get_status(const mb_sdk *s){return &s->status;}
