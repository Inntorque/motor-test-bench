#ifndef MB_SDK_H
#define MB_SDK_H
#include <stdint.h>
#include <stddef.h>
#include <stdatomic.h>
#include "mb_protocol.h"
#define MB_SDK_API 0x010000u
#define MB_RX_DEPTH 32u
#define MB_OK MB_RESULT_OK
#define MB_PENDING MB_RESULT_PENDING
#define MB_UNSUPPORTED MB_RESULT_UNSUPPORTED
#define MB_INVALID_ARGUMENT MB_RESULT_INVALID_ARGUMENT
#define MB_OUT_OF_RANGE MB_RESULT_OUT_OF_RANGE
#define MB_NOT_READY MB_RESULT_NOT_READY
#define MB_BUSY MB_RESULT_BUSY
#define MB_STALE_SESSION MB_RESULT_STALE_SESSION
#define MB_STALE_GENERATION MB_RESULT_STALE_GENERATION
#define MB_DEVICE_FAULT MB_RESULT_DEVICE_FAULT
#define MB_TIMEOUT MB_RESULT_TIMEOUT

typedef struct { uint16_t id; uint8_t data[8]; } mb_can_frame;
typedef struct { uint8_t state,mode; uint16_t flags; } mb_state;
typedef struct { uint64_t time_us; int64_t position; uint32_t fields,valid,estimated,warning; int32_t value[32]; } mb_snapshot;
typedef struct { uint8_t mode; int32_t direction,iq_ma,negative_id_ma; uint32_t phase_ma,bus_ma,bus_mv,speed_mrpm; int32_t velocity_mrpm; int64_t position_microturns; } mb_run_config;
typedef struct { uint32_t api; uint8_t node; uint32_t watchdog_ms; } mb_config;
typedef struct {
    void *user;
    int (*can_try_send)(void *,const mb_can_frame *,uint8_t priority);
    uint64_t (*clock_us)(void *);
    uint64_t (*boot_identity)(void *);
    int (*read_descriptor)(void *,uint16_t,uint8_t *,uint16_t *);
    void (*read_state)(void *,mb_state *);
    int (*read_snapshot)(void *,mb_snapshot *);
    int (*prepare_run)(void *,const mb_run_config *,mb_run_config *);
    int (*request_start)(void *,uint32_t);
    int (*set_target)(void *,uint16_t,int64_t);
    void (*request_disable)(void *,uint16_t);
    void (*heartbeat)(void *);
    int (*clear_fault)(void *);
    int (*read_diagnostic)(void *,uint16_t,uint8_t *,uint16_t *);
    int (*read_frozen_chunk)(void *,const uint8_t *,uint16_t,uint8_t *,uint16_t *);
} mb_port;
typedef struct {
    uint8_t *rx,*tx; uint16_t capacity,rx_size,rx_at,tx_size,tx_at,tx_block;
    uint8_t rx_sn,rx_block,tx_sn,tx_wait,waits,rx_flow;
    uint64_t rx_deadline,tx_deadline,tx_next; uint32_t stmin;
} mb_isotp;
typedef struct { uint32_t request; uint16_t request_size,response_size; uint8_t request_bytes[160],response_bytes[160]; } mb_dedup;
typedef struct {
    uint64_t boot,session; uint32_t generation,command_seq,last_stop_id;
    uint32_t rx_dropped,protocol_errors,sample_dropped,stops; uint8_t claimed,prepared;
} mb_sdk_status;
typedef struct {
    mb_config config; mb_port port; mb_sdk_status status;
    mb_isotp channel[2]; uint8_t control_rx[1024],control_tx[1024],diag_rx[256],diag_tx[256];
    mb_can_frame queue[MB_RX_DEPTH]; atomic_uint head,tail,stop_serial,stop_id;
    uint32_t handled_stop; uint64_t last_heartbeat,next_status,next_device,next_sample;
    mb_run_config run; uint32_t prepared_id,period_us; uint16_t sample_seq;
    mb_snapshot snapshot; uint32_t sample_pending; uint8_t sample_end;
    mb_dedup dedup[4]; uint8_t dedup_next;
} mb_sdk;
int mb_sdk_init(mb_sdk *,const mb_config *,const mb_port *);
void mb_sdk_on_can_rx_isr(mb_sdk *,const mb_can_frame *);
void mb_sdk_poll(mb_sdk *,uint64_t now_us);
void mb_sdk_publish_snapshot(mb_sdk *,const mb_snapshot *);
int mb_sdk_report_event(mb_sdk *,uint16_t,uint8_t,uint8_t);
const mb_sdk_status *mb_sdk_get_status(const mb_sdk *);
uint16_t mb_get16(const uint8_t *);
uint32_t mb_get32(const uint8_t *);
uint64_t mb_get64(const uint8_t *);
void mb_put16(uint8_t *,uint16_t);
void mb_put32(uint8_t *,uint32_t);
void mb_put64(uint8_t *,uint64_t);
int mb_tlv(uint8_t *,uint16_t *,uint16_t,uint16_t,uint8_t,const void *,uint16_t);
int mb_number(uint8_t *,uint16_t *,uint16_t,uint16_t,uint8_t,uint64_t);
#endif
