# TypeScript 浏览器架构

当前在线产品的自主维护代码以 TypeScript 为主：React 界面使用 TSX，设备访问、测试 Worker、数据处理、总网关及 Bootloader 使用 TS。浏览器接收 Vite 编译后的 JavaScript，不下载 TypeScript 编译器，不启动本机通信服务。

## 分层与入口

| 层 | 源码 | 职责 |
| --- | --- | --- |
| 总网关 | `gateway/home.ts`、`gateway/icon-motion.ts` | APP 入口、版本链接与悬停效果 |
| 工作台界面 | `web/src/*.tsx`、`web/src/*.ts` | 原操作布局、图表、配置选择和导出 |
| 浏览器主线程 | `browser/bootstrap.ts`、`browser/hardware.ts` | 用户授权、Web Serial / WebUSB、页面请求与 Worker 消息 |
| 测试执行 | `browser/worker.ts`、`bench.ts`、`control.ts`、`specialized.ts` | 既有测试、记录、停止确认和状态发布 |
| 协议与数据 | `browser/mbp-codec.ts`、`transports.ts`、`rs485.ts` | 沿用 MBP/1、CAN、Modbus 字节及限值 |
| 独立维护 APP | `gateway/ui/bootloader/*.ts` | J-Link / SWD、备份和直接烧录、总进度 |

`browser/messages.ts` 定义主线程与 Worker 的消息种类、请求、设备输入和 IO 参数；`browser/types.ts` 定义测试运行状态。界面复用执行器的返回类型，避免再手写一套同名状态。运行时的协议字段、寄存器、命令和校验继续使用既有实现。

移植自原电机工具的两个 HTML 页面仍作为原有静态页面模板保留，原工程、Python 参考解码器、C SDK 和冻结协议文件不随本次迁移修改。Python 仅保留既有离线构建、参考资料提取和维护用途，在线控制由浏览器执行。

## 类型检查边界

- `tsconfig.json` 检查全部自主维护的 TS / TSX：属性、参数、模块引用和返回值，禁止在这些源目录混入 JS。旧动态状态保留渐进式设置 `strict: false`，本次不宣称全工程已完成严格类型收紧。
- `tsconfig.strict.json` 对 Bootloader 全部模块、字节/串行队列、消息与运行状态类型、环形帧缓存和编码器分箱执行严格检查。历史错误对象沿用 `useUnknownInCatchVariables: false`。
- `tsconfig.worker.json` 使用 WebWorker 标准库独立检查测试执行入口；`tsconfig.tools.json` 单独检查 Node 构建配置，避免混用页面和 Worker 的全局环境。
- 不使用生产源码中的 `@ts-ignore`、`@ts-nocheck` 或 `noCheck` 跳过错误。`npm test` 和 `npm run build` 都先运行类型检查；Node 直接执行测试时只擦除类型，不能代替 `tsc`。

类型检查前由 `scripts/generate_browser_metadata.mts` 调用原只读提取器生成旧工具的参数元数据；直接构建浏览器包也执行这一前置步骤。干净检出不依赖 `.runtime` 历史缓存。

开发环境要求 Node **22.18+**。普通维护、安装依赖和构建沿用仓库入口：

```sh
npm ci
npm run typecheck
npm test
npm run build
python3 scripts/build_gateway.py
npm run preview:gateway
```

最后一条只在 `127.0.0.1:5180` 提供已构建的静态网页预览；端口被占时直接报错。测试电脑使用 GitHub 网页与浏览器设备授权，不需要这些开发命令。

## Rust / WebAssembly 使用范围

本轮没有引入 Rust crate、WASM 下载器或未使用的兼容层。现有设备等待和电机图形绘制分别由浏览器 IO 与 WebGL / CSS 负责；迁移 TypeScript 不等于已经测得 CPU 热点。

后续若性能测量确认批量拟合、频域分析或大规模数值处理成为瓶颈，可将对应纯计算函数局部实现为 Rust / WebAssembly；输入、输出和精度需与 TypeScript 版本逐项对照。设备授权、控制状态机、启动/停止命令与 SDK 协议仍留在现有接口边界，不能因引入计算模块改变它们。当前未增加这类运行时切换代码。

## 验证

迁移验收包含全工程及严格模块类型检查、既有 JavaScript 行为回归、协议固定向量、两个原解码器对照、浏览器连接/控制/记录导出、Bootloader 两种模拟烧录与总进度、图标居中与空闲/隐藏暂停。软件回归不代表对实物执行了电机输出或 Flash 擦写。

## 保留的原有显示问题

迁移检查发现：`App.tsx` 的两处 `loadEnabled` 派生值读取 `supplyMeasurements()` 返回对象的 `status`，而该显示帮助函数当前没有返回此字段。因此已取得电源读数时，该派生值仍可能显示为未加载。为保留本次迁移前的界面行为，没有修改这一取值；设备原始状态仍在 `device.snapshot.status`，输出命令和设备确认不依赖新增类型声明。该问题应单独修正并验证记录字段。
