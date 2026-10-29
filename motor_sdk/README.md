# MotorBench 驱动板 SDK 1.0.0 测试交付

纯 C11、静态内存、经典 CAN / MBP 1.0。当前 N32G455 端口已编译，Python 与实际编译 C SDK 的离线联测通过；当前板实测、第二块实物驱动板验收仍待进行。本次不宣称已完成两块板的正式发布验收。

SDK 只负责电机业务通信，不管理固件镜像、A/B 槽位或 OTA。驱动工程可沿用自己的 Bootloader、升级和回退实现；它们不改变工作台对 MBP 的接入方式。

## 接入

1. 将 `include/`、`src/` 固定在驱动工程的第三方依赖目录，或通过 CMake 加入库。
2. 实现 `mb_port`，静态分配一个 `mb_sdk`，以 `MB_SDK_API`、节点 1..63、设备实际通信监督时间调用 `mb_sdk_init`。
3. CAN RX 仅将 DLC=8 的标准数据帧交给 `mb_sdk_on_can_rx_isr`。两个 RX 中断须构成同一个、不会互相嵌套的生产者；原子操作要求目标的 32 位原子操作无锁。当前 N32 两 FIFO 中断同为优先级 6。
4. 一个通信任务周期调用 `mb_sdk_poll`，当前板周期 1 ms。其他 SDK API 在该任务执行；FOC ISR 只更新板端自己的完成快照，端口在短临界区复制。
5. `can_try_send` 不等待邮箱，失败返回非零，由 SDK 下次推进。保持 STOP/状态先于诊断发送；已有在途硬件帧不能撤回。回调不保存 Flash，不重调 FOC，不虚构测量值。
6. 先做只读 `IDENTIFY`、状态和测量验收，再由操作者显式控制运动。

`examples/n32g455` 包含当前实际端口与集成差异；其 FOC/HAL 依赖只属于该板，不属于 SDK 核心。独立控制器示例在 `tests/simulator.c`，仅用于离线测试。

## API 与端口

公共定义见 `include/mb_sdk.h`。必须实现 CAN 发送、时钟、启动身份、描述对象、实际状态、已完成快照、运行准备、启动、目标更新、停机回调。心跳回调可转交已有板端监督；清故障、诊断和冻结块回调按真实能力实现。

- `mb_run_config`：电流 mA，电压 mV，速度 0.001 rpm，位置 0.000001 转。
- `mb_snapshot`：0..31 字段位图；有效、估算和质量告警独立。64 位时间/位置由 SDK拆帧，未知量不设有效位。
- `mb_state`：实际输出与状态分开。`request_start` 返回接受不代表实测达到目标。
- `request_disable` 在任务内执行。当前端口调用原 `foc_motor_ctrl(0)`；实机最大停止延迟尚待测量。
- 缺少相应回调时不得在描述对象中声明可选能力。dq 电流按幅值不变 Clarke/Park 的相电流峰值等效 mA 声明。

核心上下文含控制 1024 B / 诊断 256 B 的收发缓冲、32 帧接收队列、独立 STOP 槽及四条精确命令去重记录。状态改变请求上限 160 B，覆盖当前定义的运行配置；超限拒绝，不截断。读对象 PDU 上限仍为 1024 B。无堆分配。

## 协议与版本

唯一编号源是上级 `protocol/schema.json`。修改后运行 `python3 protocol/generate.py`，同步 C/Python 常量、对象表与 DBC；固定向量见 `protocol/golden.json`。

线协议主版本不兼容时禁止运动；引导 IDENTIFY 与固定 `STOP + u32` 不改变语义。SDK API 是源码兼容承诺，不承诺跨 MCU 的二进制 ABI。厂商固件版本仅作追溯，不作为主机功能开关。

控制事务须带 boot/session/generation/sequence。PREPARE 不使能，START 引用准备结果。STOP 无须会话，独立入槽，撤销旧准备与旧代际。未知动作结果不自动重发。每个 ISO-TP 通道独立推进，诊断不占控制事务锁。

首版提供固定 20 ms 反馈配方，`SET_STREAMS` 空配置返回实际周期；其他配方返回 UNSUPPORTED，不假装已采用。温度在当前板同包发送，但带独立转换源时间；不能称为与 dq 电流同拍。

## 验证

仓库根执行 `python3 -m unittest discover -s tests -p 'test_mbp*.py' -v`，会用系统 C 编译器编译 SDK 模拟端口，再由 Python 通过完整 CAN/ISO-TP 交换。没有串口、CAN 或电机动作。MCU 资源报告和实机待测项见 `docs/motor-sdk-implementation.md`。
