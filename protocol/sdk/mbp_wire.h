#ifndef INNTORQUE_MBP_WIRE_H
#define INNTORQUE_MBP_WIRE_H
#include <stddef.h>
#include <stdint.h>
#include "mbp_ids.h"
#define MBP_HEADER_SIZE 16u
#define MBP_MAX_PDU 1024u
typedef struct {uint8_t major,minor,opcode,flags;uint32_t request;const uint8_t *body;uint16_t body_size;} mbp_packet;
typedef struct {uint16_t id;uint8_t type;const uint8_t *value;uint16_t size;} mbp_tlv;
/* Returns bytes written, or 0 on insufficient capacity/invalid arguments. */
size_t mbp_encode(uint8_t *out,size_t capacity,uint8_t opcode,uint8_t flags,uint32_t request,const uint8_t *body,size_t size);
/* Decodes a complete ISO-TP PDU; version compatibility is checked by the caller. */
int mbp_decode(const uint8_t *data,size_t size,mbp_packet *out);
size_t mbp_put_tlv(uint8_t *out,size_t capacity,uint16_t id,uint8_t type,const uint8_t *value,size_t size);
/* 1 = item, 0 = end, -1 = invalid. offset advances only on success. */
int mbp_next_tlv(const uint8_t *data,size_t size,size_t *offset,mbp_tlv *out);
void mbp_stop_frame(uint8_t out[8],uint32_t stop_id);
#endif
