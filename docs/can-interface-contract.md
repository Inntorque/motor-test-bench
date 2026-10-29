# 当前 CAN 接口契约

当前接口为 MBP/1：经典 CAN、500 kbit/s、11 位标准帧、DLC=8、小端整数，原生节点 1..63。当前板 SDK 节点 1，OTA 维护节点 0 单独保留。

唯一字段定义见 [schema](../protocol/schema.json)、[对象表](../protocol/objects.md)、[DBC](../protocol/mbp.dbc)、[固定向量](../protocol/golden.json)。固定 STOP 为 `0x080+n`，载荷 ASCII `STOP` + stop_id u32，不需要活动会话。

对象消息通过双通道 ISO-TP：控制 PDU 1024 B、诊断 PDU 256 B。完整接口、兼容性与当前实现范围见 [固化方案](motor-protocol-sdk-plan-v1.md) 和 [实施交付](motor-sdk-implementation.md)。

[早期 CANable/Web Serial 规划已归档](can-interface-contract-legacy.md)，不作为当前运行契约。
