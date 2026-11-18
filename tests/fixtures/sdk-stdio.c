#include "mb_sdk.h"
#include <stdio.h>
#include <stdlib.h>

void *sim_create(void);
void sim_destroy(void *);
void sim_receive(void *,uint16_t,const uint8_t *);
void sim_step(void *,uint64_t);
int sim_pop(void *,uint16_t *,uint8_t *);

int main(void)
{
    void *sim=sim_create();
    char line[128];
    if(!sim)return 1;
    while(fgets(line,sizeof line,stdin)){
        uint16_t id;
        uint8_t frame[8];
        unsigned long long stamp;
        if(sscanf(line,"tick %llu",&stamp)==1)sim_step(sim,(uint64_t)stamp);
        else{
            unsigned address;char payload[17];
            if(sscanf(line,"rx %x %16s",&address,payload)!=2)return 2;
            for(unsigned i=0;i<8;i++){
                unsigned octet;
                if(sscanf(payload+2*i,"%2x",&octet)!=1)return 2;
                frame[i]=(uint8_t)octet;
            }
            sim_receive(sim,(uint16_t)address,frame);
        }
        while(sim_pop(sim,&id,frame)){
            printf("%x ",id);
            for(unsigned i=0;i<8;i++)printf("%02x",frame[i]);
            putchar('\n');
        }
        fflush(stdout);
    }
    sim_destroy(sim);
    return 0;
}
