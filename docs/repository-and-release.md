# 仓库与发布管理

沿用 `Inntorque/motor-test-bench`，同时维护 Windows x64 与 Ubuntu x64，不拆成两套业务源码。远端默认分支为 main。当前本地工作分支 motor-test-bench；本轮包含先前已授权的旧网关/旧说明清理及桌面应用整理，历史提交不重写。

## 文件归属

| 位置 | 保存内容 | 不保存内容 |
| --- | --- | --- |
| Git 源码仓库 | desktop、browser、web、protocol、profiles、tests、scripts、docs、锁文件、CI | EXE、ZIP、node_modules、运行日志、真实测功数据、签名私钥 |
| protocol/ | 唯一 schema、接入规范、C11 设备 SDK 与 C99 编解码、黄金报文、验收清单 | 单台设备的用户参数和原始记录 |
| docs/releases/ | 每版变更说明与验收结论 | 大型构建产物或重复旧操作手册 |
| GitHub Releases | 两个平台的 portable ZIP、增量版本 ZIP（完整程序版本）、签名清单 | 开发依赖、生产记录、私钥 |
| 本地 artifacts/releases/vX.Y.Z/ | 待审核发布附件 | 不纳入 Git |
| 本地 .runtime/ | 验证日志、截图、上传审核清单、临时工具链 | 不纳入 Git |

“更新 ZIP”包含完整的新版本程序，不是二进制差分；它不包含启动器、用户 config 和测功数据。根启动器/信任公钥升级另行部署完整便携包，不能通过普通应用更新默默替换。

## 版本和签名

应用使用 x.y.z；根 package.json、desktop/package.json 和对应锁文件同时更新，变更说明为 docs/releases/x.y.z.md。协议当前独立版本 1.1.0、wire 1.0；修改已有对象语义必须升协议主版本，应用更新不自动升级驱动固件。

发布脚本使用 Ed25519 签名原始 manifest payload，并写入程序 ZIP 的 SHA-256、字节数、平台、架构、最低启动器版、协议主版和数据格式版。仓库只跟踪 desktop/release-public-key.pem。当前开发机私钥保存在仓库之外 `~/.local/share/inntorque-release/ed25519-private.pem`，权限 0600；应另行保管离线备份，不写进仓库、工件或日志。丢失私钥将不能向现有客户端发布可信更新。

首次部署已固定公钥，不能再次运行 release-key 脚本覆盖；脚本也会拒绝覆盖现有密钥。需要新密钥时应明确执行换钥部署。清单签名不替代 Windows Authenticode，当前尚无商业代码签名证书。

## 推送与发布的许可边界

1. 本地实现、检查、构建，运行 `npm run review:upload` 生成路径、大小、SHA-256 和删除清单。脚本不暂存、不提交、不上传；发现产物、记录或私钥会失败。
2. 交付用户审查，单独获取代码推送许可。建议仅推送经审查的 motor-test-bench 分支，再通过 PR 合入 main；不强推、不重写历史。当前尚未提交或推送本轮改动。
3. 推送经批准的 vX.Y.Z 标签会自动触发双端验证、签名构建和草稿创建。也可对已有标签手动运行 Prepare approved release draft，填写 CREATE_DRAFT。
4. 在 GitHub 配置 release 与 release-publish 环境的审批人和标签规则，并将 INNTORQUE_RELEASE_PRIVATE_KEY 设置为 release 的环境密钥。普通分支提交只执行验证。
5. 正式发布获批后运行 Publish verified desktop release，填写标签和 PUBLISH。工作流会下载草稿附件，核对两个平台及 SDK 的签名、大小和 SHA-256，再将草稿发布为最新稳定版。
6. 测试电脑启动/定时/手动检查正式 Releases。更新需点击确认，输出关闭且记录保存后切换。开发端推送不是强制终止现场测试的命令。

若已有同版本标签/Release，工作流不覆盖附件；更正正式版本必须升版本号。禁止使用无签名附件绕过客户端校验。两端验证任意一项失败，草稿任务不能执行。

## 可复现命令与外部依赖

Node 22.23.4、Go 1.27.4、Electron 45.3.3，npm 锁文件固定依赖。本地 `scripts/release.mjs` 可在 Ubuntu 构建 Windows 包，但交叉构建不能替代 Windows 运行测试。独立验证工作流覆盖 Ubuntu 24.04、Windows Server 2022 runner，使用一次性测试密钥构建便携包并验证启动、升级和回退（不使用生产密钥，不上传测试附件）；现场 Windows 电脑仍需适配器驱动和真实设备验收。

GitHub Actions 使用官方 [checkout](https://github.com/actions/checkout)、[setup-node](https://github.com/actions/setup-node)、[setup-go](https://github.com/actions/setup-go)；正式发布使用 GitHub CLI 创建草稿。源码审查清单不等于 Git 历史的敏感信息审计，不删除或改写历史。
