# Bootloader 网页 APP

## 使用

从测试总网关选择 **Bootloader**，使用桌面版 Chrome / Edge。该 APP 在浏览器内通过 WebUSB 直接连接 J-Link，不启动本机服务，不调用桌面脚本、OpenOCD 或 Python。

1. 结束其他 APP 的测试并停止输出。将 J-Link 接到 SWDIO、SWCLK、GND、3.3 V VTref，目标板自行供电。
2. 将“连接选择”设为“连接”，首次在浏览器弹窗选择 J-Link。保持此选择时，APP 持续自动识别芯片，失败后间隔 1 秒重试，连接成功后持续只读检查；同一探针重新插入时自动重连，不反复弹出授权窗口。需要换探针时点击“选择 J-Link”。此步骤只初始化 SWD 调试口并读取标识，不暂停、复位或擦写目标。
3. 核对 N32G455、512 KiB Flash、144 KiB SRAM、目标电压及发布镜像。固定 SWD 速率为 100 kHz。
4. 点击“备份并烧录 Bootloader”。APP 暂停芯片，读取全部 512 KiB Flash，将备份保存到当前浏览器 IndexedDB；保存完成前不会擦写。
5. 只擦写发布镜像涉及的 Boot 页面，并恢复最后一页镜像范围外的原字节。完整回读 512 KiB，与“原备份 + 新 Boot 镜像”逐字节核对；一致后才复位。
6. 导出全片备份和操作日志，另行保存。浏览器本站点数据不是永久归档；清除站点数据会删除备份。

选择“不连接”停止自动识别与重试，当前传输收尾后释放本 APP 打开的探针；刷新页面默认“不连接”。自动重连只使用操作者选定、已授权的同一探针；没有序列号且浏览器重新枚举为另一设备对象时，需要手动确认，不猜测其他探针。相同失败原因只记录一次，设备区继续显示重试次数。浏览器后台限速或休眠时不保证 1 秒调度。

“只读取备份”会暂停目标并保留暂停状态，不写 Flash；可导出备份，再由操作者点击“恢复运行”。备份、烧录及恢复运行期间先等待连接探测完成，再暂停自动探测；操作结束后恢复，避免并发访问 USB/SWD。连接选择在这些操作期间禁用。烧录失败且尚未完成校验时不自动复位或恢复运行，不执行整片擦除、读保护解除、Option Bytes 修改或应用升级。中断后保留日志和备份，通过原 SWD 维护工具检查、恢复。

烧录期间保持标签页、USB 和电源连接，不刷新或休眠。网页无法在断线、强制关闭或电脑休眠后继续完成烧录。普通 SWD 无法识别时，设备区显示失败阶段和应答：`ACK=4 (FAULT)`、`ACK=2 (WAIT)` 或无有效应答；保持“连接”会继续只读尝试。重试不自动触发烧录或续传，不能据此保证接线或板端调试通路已恢复。已有桌面维护脚本的 `--under-reset` 是需要接 NRST 的独立恢复方式，网页不自动拉复位或尝试解锁。

## 实物连接验证（2026-11-03）

J-Link 的 `0xCF` SWD 返回位图中，ACK 从请求起点的第 8 位开始，读取数据从第 11 位开始，奇偶位为第 43 位。原网页错误地从第 9 / 12 / 44 位取值，把正常应答误报为 `ACK=4 (FAULT)`；本次只修正返回解码，不改变实际请求、方向或 turnaround 时钟。回归使用当前 J-Link V9 捕获的原始字节 `00 b9 a3 00 5d 01 00`，解码为 DPID `0x2BA01477`。

用网页相同的 J-Link / SWD JavaScript 模块经临时 USB 管道做只读实测，连续 10 次读取 CPUID `0x410FC241`、DBG_ID `0x8F584511`，均识别为 N32G455 / 512 KiB / 144 KiB；独立 OpenOCD 读取标识一致。USB 管道仅用于本次诊断，不进入网页或发布包，正常网页继续直接使用 WebUSB。

未停核的大块 Flash 读取仍偶发奇偶校验失败，独立 OpenOCD 也在大块读取中失败；当前证据不足以确定探针、接线或运行中目标状态的具体影响。保留原校验和停止报错行为，不跳过失败数据，不增加自动擦写或复位。本次未暂停、擦写或复位目标，连接识别通过与实物烧录验收分别记录。

## 镜像与分区

发布文件来自 `Template/CAN_OTA` 当前已校验构建，不修改该工程固件源码，也不修改 `Template/N32G455REL7_Template`。本次镜像为 2026-11-03、10,592 B，SHA-256：

```text
8842b997016f2e6ecae00e7af0a7109de0748a1b6ad34efcf2e73c17de6f9f1d
```

固定 Boot 分区为 `0x08000000–0x08007FFF`，页大小 2 KiB。芯片标识使用 N32 手册 DBG_ID 寄存器 `0xE0042000` 的高/中/低型号及容量字段，不使用 STM32 Flash 容量地址。App A/B、OTA 元数据、用户参数及 Boot 未覆盖字节在复位前逐字节保持原值。复位后原 Boot / App 会按既有启动协议维护元数据，Bootloader 不是电机应用；板上原应用是否匹配各上位机仍需单独核对。

这里的 App A/B 保留指本工程分区地址内的字节不改。当前板上旧固件布局尚未确认；若原固件是从 `0x08000000` 启动的单体应用，它的前 32 KiB 与新 Boot 分区重叠，烧录后需再 OTA 匹配本布局的电机应用，不能保证旧程序继续启动。

镜像随 APP 发布在 `gateway/ui/bootloader/public/firmware/`，包含 BIN、HEX 和记录来源文件 SHA-256 的 `manifest.json`。运行时核对目标、分区、体积、向量和 BIN SHA-256。后续更新只替换这一组经过编译验证的发布文件并更新清单；CI 不编译 MCU，不从测试电脑本地路径读取镜像。构建和 UI / 擦写回归不等于实物烧录验收。

固件依赖的许可与版权说明随镜像保存在 `firmware/NOTICE.txt` 和 `firmware/licenses/`，不分发 MCU 编译器或本机烧录工具。

## 代码与维护

页面、J-Link USB、SWD / MEM-AP、N32 擦写分别位于 `gateway/ui/bootloader/`。构建输出独立、资源带内容指纹，不加载三个测试 APP 的 Worker，不修改电机 SDK、CAN 协议或电机应用固件。总网关通过 `gateway/apps.json` 新增入口，旧 Python 本机服务不承载该 APP。

WebUSB 仍受操作系统 USB 权限、独占访问和驱动约束。Ubuntu 需具备探针 USB 访问权限；Windows 需使用浏览器可访问的 USB 驱动。网页不自动安装驱动、修改系统权限或探针固件，也不能跳过浏览器设备授权弹窗。Firefox / Safari 不支持该直连入口。

技术依据：[Chrome WebUSB](https://developer.chrome.com/docs/capabilities/usb)、[libjaylink SWD I/O 协议](https://openocd.org/doc-release/doxygen/swd_8c_source.html)、[libjaylink 接口定义](https://openocd.org/doc-release/doxygen/libjaylink_8h.html)、ARM ADIv5 / Cortex-M4 调试寄存器、CAN_OTA 工程内 N32G45x 手册及厂商 Flash 驱动。SEGGER 的 [J-Link OB WebUSB 烧录](https://kb.segger.com/J-Link_WebUSB)要求预置 Flash Loader，不能将该功能当作普通 J-Link V9 的通用 N32 烧录能力；本 APP 使用 SWD / N32 寄存器流程。
