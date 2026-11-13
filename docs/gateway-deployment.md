# Inntorque 测试总网关与浏览器直连

## 网页入口与使用

打开 [Inntorque 测试总网关](https://inntorque.github.io/motor-test-bench/)，选择测功机、7010 上位机、5014 上位机、8513 上位机或独立 Bootloader APP。四个电机测试 APP 的完整页面、控制执行器和设备通信代码均从 GitHub Pages 加载，在浏览器内运行。测试电脑不安装 Python、Node.js 或运行包，不启动本机 HTTP 服务，不请求 5181、5173、8892、8888 等回环端口。旧桌面启动脚本不再是公网网页的运行条件。

使用桌面版 Chrome 或 Edge，通过 HTTPS 打开。点击顶部“授权串口”，在浏览器弹窗选择 USB-CAN 串口或 USB-RS485；多台设备需分别授权。gs_usb 类适配器使用“授权 USB-CAN”。授权只取得访问许可，不打开设备、不发送配置、不启动输出。浏览器要求用户点击授权，网页无法跳过这一步。参见 [Web Serial 官方说明](https://developer.chrome.com/docs/capabilities/serial)和 [WebUSB 官方说明](https://developer.chrome.com/docs/capabilities/usb)。

授权后，在现有连接配置中选择对应接口，再连接验证。CAN 波特率为 500 kbit/s。测功机默认 MBP 节点 1，7010、5014、8513 在各自的连接面板选择适配器和节点，默认原生 FOC 协议 v2 的节点 0。不要将同型号的两个电源串口互换；浏览器不会根据相同 VID/PID 猜测供电对象，刷新后需要重新选择授权接口。

测功机的一键连接、两阶段加载、最大输出、专项测试、电机/驱动板名称和裸机/减速箱配置保持原界面和控制含义。万瑞达电机电源连接只读，开启前核对面板 24 V / 20 A 预设；制动电源保留原 SSJ 缩放和限幅。传感器保留 Modbus RTU 与 HEX6/HEX8 只读采集。浏览器不能访问任意原始 TCP 端口，本版本使用 USB-RS485，不提供旧 RTU 透明 TCP 网关选项。

Bootloader APP 使用 J-Link / SWD 单独维护发布镜像，流程为识别、备份、仅更新 Boot 和全片回读，不运行电机测试。使用方法及验证边界见 [Bootloader 网页 APP](bootloader-app.md)。

## 适配器与权限

支持 ZQWL USB 串口 CAN（串口 3 Mbit/s）、CANable2 的 SLCAN 串口固件（VID:PID `16d0:117e`、串口 115200 / 8N1）和 gs_usb USB-CAN，均发送原有 Classic CAN 帧。CANable2 与 ZQWL 均点击“授权串口”，CANable2 识别后自动走 SLCAN，不使用 gs_usb 的 USB 筛选。CAN 总线仍为 500 kbit/s；串口数字不代表 CAN 波特率。CANable2 原固件没有配置 ACK，软件读取适配器版本后配置通道，再以板端真实应答判断设备连接，不能将串口打开当成板端连接成功。命令依据 [CANable2 原固件说明](https://github.com/normaldotcom/canable2-fw#supported-commands)。浏览器直连并不免除操作系统设备驱动、串口权限或 USB 接口权限：Ubuntu 用户仍需具备串口/USB 访问权限；Windows 的原生 gs_usb 接口需可由 WebUSB/WinUSB 访问。系统已占用接口、其他 APP 已打开设备或接口驱动不匹配时，浏览器会返回实际打开错误，不接管其他程序。

电机 APP 在点击连接后打开已授权的适配器。7010、5014、8513 会先核对板端回复的型号，再读取反馈。切换 APP 前，先停止并确认输出关闭，再断开当前连接。测功机继续依据 MBP/1 能力声明接入。

## 记录与页面生命周期

测试配置、对象名称和新记录存入当前浏览器、当前站点的 IndexedDB。测功机保留 CSV、JSONL 和专项报告导出。5014、8513 采集测功诊断，保存板端元数据、采样与停机快照，可在各自页面导出最近一次记录为 JSON。8513 的三相电流波形使用板端诊断采样。

不同电脑、不同浏览器和无痕窗口的数据相互独立。清除网站数据会删除这些浏览器记录，重要结果应导出保存。原 Python 版本的磁盘记录不会删除，但浏览器不能无授权读取旧 `recordings/`、原串口路径或本机 JSON 配置，也不会自动导入它们。

测功机的正常停止沿用原有命令取消代际、关闭读回、最新状态确认及电源断电兜底。开启输出前写入状态日志；重新加载后若上次关闭未确认，会要求重新连接原设备并停止核对，不伪造关闭成功。设备声明的保护、最大输出分配与 SDK 心跳没有修改，诊断质量标志不额外变成停机条件。

纯网页的执行生命周期与标签页一致。测试时保持页面打开，不刷新、不切换 APP、不让电脑休眠；正常退出前先点击停止并确认。浏览器崩溃、强制关闭或系统休眠时，网页不能保证继续发送控制或完成 RS485 电源 OFF；电机驱动固件已有的失联停机机制仍由板端执行，不能据此承诺程控电源自动断电。网页更新不会重载正在运行的标签页，下一次正常打开才加载新版。

## 固件和维护边界

`protocol/` 的 MBP/1 定义、`motor_sdk/`、板端固件及原模板工程不因本次迁移改变。浏览器执行器在 `browser/`，把原主机端通信及测试流程移植到 JavaScript；它不向电机固件加入新的协议、阈值或弱磁参数。

7010 原工程的固件升级、回退、复位及复位清故障通过原工程上位机执行。5014、8513 保留原生协议的清故障操作。测功诊断的本机分析脚本仍在各自工程运行，门户提供浏览器 JSON 导出。

新版页面分别由桌面 `7010_foc`、`5014_FOC`、`8513_foc` 中的 `N32G455REL7_Template/web` 导入至 `gateway/ui/foc-*`。入口清单在 `gateway/apps.json`，CAN v2 执行器在 `browser/foc.ts` 和 `browser/foc-protocol.ts`。旧页面和 Python 参考源码留在仓库供回归使用。

## 构建、校验和部署

以下命令只由维护/CI 电脑执行，测试电脑不需要：

```sh
python -m pip install -r requirements-motor.txt
npm ci
npm run build
python scripts/build_gateway.py
npm test
npm run test:browser
npm run test:gateway-icons
python -m unittest discover -s tests -p 'test_*.py'
```

`dist/` 是测功机基础网页构建；`site/` 才是公网交付内容，包含总网关、四个电机测试 APP、Bootloader APP 和浏览器执行器。构建时注入独立运行入口与内容版本，清理已识别的旧输出目录，不包含本机服务安装包。旧网页 ZIP 作为原界面回归参照保留，不是运行依赖。`.runtime/`、`site/`、设备配置及现场数据均不提交。

`motor-test-bench` 为开发分支，已校验版本同步到同一仓库 `main`；Pages 工作流只监听 `main`，运行构建、协议/控制/记录测试和隔离浏览器测试，再发布原网址。移除原 Windows 本机服务打包前置条件，避免网页发布继续依赖已取消的运行包。已有 Windows 历史工具发布流程不由 Pages 触发。

软件测试使用虚拟设备，覆盖原 SDK 金向量、分片、完整采集、最大输出、停止、记录和在无本机服务时打开四个电机 APP。不能据此宣称 Ubuntu/Windows 实物 USB-CAN、RS485、电机固件和全程负载测试已经验收。正式发布后须在测试电脑授权实际设备并验证。
