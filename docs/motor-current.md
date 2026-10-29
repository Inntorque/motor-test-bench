# 电机控制与最大输出接口

当前实现见 [统一协议与 SDK 实施交付](motor-sdk-implementation.md)、[协议对象表](../protocol/objects.md) 和 [SDK 移植说明](../motor_sdk/README.md)。

工作台原生使用 MBP/1，经当前 ZQWL USB-CAN 直连。连接只读识别，不开启输出；PREPARE 校验易失运行配置，START 必须引用准备结果并确认新鲜 RUNNING，STOP 独立撤销旧会话代际。驱动板声明能力与限值，上位机不再按厂商版本号或私有参数编号猜测功能。

最大输出由驱动本地连续分配 Iq；请求、斜坡参考、实测和限制来源分别显示与记录。电压饱和、正常跟踪波动与诊断质量标记不增加停机条件。当前板 Id=0；无确认的负 Id 许可时不开放弱磁。

[原 8892 链路说明已归档](motor-current-legacy.md)。
