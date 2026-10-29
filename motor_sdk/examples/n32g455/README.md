# 当前 N32G455 / SG-6010HB 端口

实际工程为 `/home/dash/Desktop/xige_foc/xige_foc`。本目录的端口文件是交付副本；`integration.patch` 是相对本轮备份的现有工程集成差异，不包含 SDK 核心或第三方 MCU 库。

将 SDK 固定至 `third_party/motor_bench_sdk`，端口置于 `ports`；Makefile 纳入两处源文件和头文件。CAN2 增加四个精确过滤 ID，SDK RX 中断分发与原 OTA 队列独立；原 OTA 节点 0、SDK 节点 1。

FOC 新增完成拍快照，保存真实 64 位累计编码器计数、dq 电流、参考、三相电流、ADC/PWM 序号及源时间。位置换算在任务执行。未改 R/L、PI、9 V、PWM 范围、100 A/s、10 kHz 或已有保护阈值；NTC 当前温度抑制与本上电周期历史跳闸锁定均保留。

IQ、速度、位置、最大输出分别映射当前本地模式；最大输出仅声明正向、负 Id=0。没有实测母线电压/电流采样，不发送相应实测有效字段，供电电流估算单独标记。机械传动比属于主机台架档案。

生产构建拒绝旧电机使能、目标、任意参数写入、SAVE/LOAD 和校准启动；旧 STOP 进入 SDK 撤销路径。CAN-OTA、维护只读与已保存校准页保留。需要重新校准时使用独立维护构建/原工程工具，不在工作台开放通用参数写口。

启动 nonce 使用 UID、ADC 与 DWT 启动变化混合，不作密码学认证保证；会话另由主机随机分配，重启后不会恢复旧控制。实机需核对反复重启识别、控制 ISR 时间、通信任务栈余量及停止延迟。

诊断 kind 1 是统一最大输出窗口；kind 2 为版本 1 的当前板 39 个 little-endian u16 完成拍诊断字，与 `foc_mode_loop_diag_coherent` 一一对应；kind 3 为停机冻结轨迹描述。冻结轨迹 schema 1：每点 40 B/20 个 u16，500 us 间隔，最多 256 点，布局沿用 `foc_max_runtime.inc`，按全数据集 CRC32 校验。
