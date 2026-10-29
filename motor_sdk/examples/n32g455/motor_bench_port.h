#ifndef MOTOR_BENCH_PORT_H
#define MOTOR_BENCH_PORT_H
#include <stdint.h>
void motor_bench_init(void);
void motor_bench_poll(void);
void motor_bench_legacy_stop(void);
int motor_bench_rx_isr(uint16_t,const uint8_t *);
#endif
