#include "mbp_wire.h"
#include <string.h>
static uint16_t get16(const uint8_t *p){return (uint16_t)((uint16_t)p[0]|(uint16_t)p[1]<<8);}
static uint32_t get32(const uint8_t *p){return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void put16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void put32(uint8_t *p,uint32_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);p[2]=(uint8_t)(v>>16);p[3]=(uint8_t)(v>>24);}
static int type_size(uint8_t type,size_t size,const uint8_t *value){
 static const uint8_t widths[]={0,1,2,4,8,4,8,1};
 if(type<1||type>10||size>65535u||(!value&&size))return 0;
 if(type<=7&&size!=widths[type])return 0;
 return type!=7||value[0]<=1;
}
size_t mbp_encode(uint8_t *out,size_t capacity,uint8_t opcode,uint8_t flags,uint32_t request,const uint8_t *body,size_t size){
 if(!out||size>MBP_MAX_PDU-MBP_HEADER_SIZE||capacity<size+MBP_HEADER_SIZE||flags&~3u||(!body&&size))return 0;
 if(size)memmove(out+16,body,size);
 memcpy(out,"MTBP",4);out[4]=MBP_WIRE_MAJOR;out[5]=MBP_WIRE_MINOR;out[6]=opcode;out[7]=flags;put32(out+8,request);put16(out+12,(uint16_t)size);put16(out+14,0);
 return size+16;
}
int mbp_decode(const uint8_t *data,size_t size,mbp_packet *out){
 if(!data||!out||size<16||size>MBP_MAX_PDU||memcmp(data,"MTBP",4)||data[7]&~3u||get16(data+14)||get16(data+12)!=size-16)return 0;
 out->major=data[4];out->minor=data[5];out->opcode=data[6];out->flags=data[7];out->request=get32(data+8);out->body=data+16;out->body_size=get16(data+12);return 1;
}
size_t mbp_put_tlv(uint8_t *out,size_t capacity,uint16_t id,uint8_t type,const uint8_t *value,size_t size){
 if(!out||!type_size(type,size,value)||capacity<size+5)return 0;
 if(size)memmove(out+5,value,size);
 put16(out,id);out[2]=type;put16(out+3,(uint16_t)size);return size+5;
}
int mbp_next_tlv(const uint8_t *data,size_t size,size_t *offset,mbp_tlv *out){
 if(!data||!offset||!out||*offset>size)return -1;
 if(*offset==size)return 0;
 size_t at=*offset;if(size-at<5)return -1;uint16_t length=get16(data+at+3);if(length>size-at-5||!type_size(data[at+2],length,data+at+5))return -1;
 out->id=get16(data+at);out->type=data[at+2];out->size=length;out->value=data+at+5;*offset=at+5+length;return 1;
}
void mbp_stop_frame(uint8_t out[8],uint32_t stop_id){memcpy(out,"STOP",4);put32(out+4,stop_id);}
