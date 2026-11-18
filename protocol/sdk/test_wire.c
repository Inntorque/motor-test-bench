#include "mbp_wire.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static void hex(const uint8_t *data,size_t size){for(size_t i=0;i<size;i++)printf("%02x",data[i]);puts("");}
int main(void){
 uint8_t data[1024],stop[8],value[]={0x2e,0xfb,0xff,0xff};mbp_packet packet;mbp_tlv tlv;size_t offset=0;
 size_t n=mbp_encode(data,sizeof data,MBP_OP_IDENTIFY,0,1,NULL,0);assert(n==16&&mbp_decode(data,n,&packet));assert(packet.request==1&&packet.body_size==0);hex(data,n);
 mbp_stop_frame(stop,0x12345678u);hex(stop,8);
 n=mbp_put_tlv(data,sizeof data,MBP_O_IQ_TARGET,MBP_TYPE_I32,value,4);hex(data,n);assert(mbp_next_tlv(data,n,&offset,&tlv)==1&&tlv.id==MBP_O_IQ_TARGET);assert(mbp_next_tlv(data,n,&offset,&tlv)==0);
 assert(!mbp_encode(data,15,1,0,1,NULL,0));assert(!mbp_encode(data,sizeof data,1,4,1,NULL,0));assert(!mbp_decode(data,15,&packet));
 offset=0;assert(mbp_next_tlv(data,3,&offset,&tlv)==-1);uint8_t bad_bool=2;assert(!mbp_put_tlv(data,sizeof data,1,MBP_TYPE_BOOL,&bad_bool,1));
 n=mbp_encode(data,sizeof data,1,0,1,NULL,0);data[14]=1;assert(!mbp_decode(data,n,&packet));
 memcpy(data,value,4);n=mbp_put_tlv(data,sizeof data,MBP_O_IQ_TARGET,MBP_TYPE_I32,data,4);assert(n==9&&!memcmp(data+5,value,4));
 n=mbp_encode(data,sizeof data,1,0,1,data,n);assert(n==25&&mbp_decode(data,n,&packet));offset=0;assert(mbp_next_tlv(packet.body,packet.body_size,&offset,&tlv)==1&&!memcmp(tlv.value,value,4));
 return 0;
}
