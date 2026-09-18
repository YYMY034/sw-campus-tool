# 运动世界校园 · PC 端一键工具

> 一个零依赖（标准库 + pycryptodome）的 PC 端自动化工具，通过服务器接口
> 完成登录、跑步记录生成与提交。纯学习逆向 / 个人测试使用。

> ⚠️ **免责声明（务必阅读）**
>
> 1. **仅限学习研究**：本工具仅供学习逆向、协议研究与个人测试使用，**请勿用于作弊**或违反校园体育规定的行为。
> 2. **严禁倒卖**：本工具**完全免费**，严禁任何形式的**倒卖、转售、付费代刷、引流收费**或商业牟利。任何以本工具名义收费售卖的行为均与作者无关，属他人侵权行为。
> 3. **作者不承担任何责任**：作者**不对使用本工具产生的任何直接或间接后果承担责任** —— 包括但不限于账号封禁、成绩作废、纪律处分、数据丢失、法律纠纷等，**一切后果由使用者自行承担**。
> 4. 请遵守学校及相关平台的使用条款。
> 5. 下载、安装或使用本工具，即视为已阅读并同意上述全部条款。
>
> 📄 完整法律条款详见 [LICENSE](LICENSE)。

---

## 🎯 功能范围声明（我们做了什么）

本工具**只区分并复现 App 自身本来就存在的两种跑步模式**，不新增、不臆造任何第三种类型：

| 模式 | 命令参数 | 服务端类型 | 判定规则 |
| --- | --- | --- | --- |
| **自由跑** | `--mode free` | `sportType=4` | 只需在**学校围栏范围内**，**不需要**经过打卡点；不携带 `fivePointJson` |
| **积分跑**（计分跑） | `--mode score` | `sportType=1` | **必须**经过服务端下发的打卡点（`isFixed=1` 为必经点），自动拉点 + 可达性校验 |

也就是说，本项目做的只是**把这两种模式的差别正确区分出来并各自生成合规轨迹**：
自由跑按校区坐标绕环、积分跑把打卡点串成闭环。除这两种模式的区分之外，
本工具**不涉及任何其他判定逻辑，也不绕过服务端的计分规则** —— 成绩是否计入、
计入多少，完全由服务端按 `runModePolicy` 的 `reasonList` 自行裁定。

**关于「点位」的明确口径** —— 两个字段都**只取学校下发的打卡点，绝不用轨迹点**：

| 字段 | 自由跑 | 计分跑 |
| --- | --- | --- |
| 提交体 `fivePointJson` | **不携带**（服务端存下来是空串） | 学校下发点位 |
| OBS 对象 `fixed_point_json` | `"[]"`（无点位） | 学校下发点位 |

轨迹点属于**另一个字段**（OBS 的 `run_data.allLocJson`），与打卡点是两回事：
轨迹点几百上千个，打卡点只有学校布设的那几个（通常 3~5 个）。

---

## 🙏 致谢

本项目的**跑步数据上传链路**参考了开源项目
**[NekoSportsWorldTool](https://github.com/YanamiNeko/NekoSportsWorldTool)**
（作者：**YanamiNeko**，以 **CC BY-NC 4.0** 许可发布）。

具体参考的部分：

- 提交接口 `POST /api/v1/run/save/record` 的请求结构
- 提交体 **31 字段签名**（`runModePolicy` / `policy_ts` / `min_distance` 等）的组织方式
- **OBS 轨迹上传**的双 key PUT 流程与 10 键协议点结构

上游为 **Rust 实现（iOS 7.3.40 链路逆向）**，本项目的 Python 版是在其基础上移植到
Android 端、并用自抓包做了字段键序校准与加密实现验证。**上传链路的主要功劳属于原作者**，
在此郑重致谢。

> 作为对照说明：加密体系（网易易盾 NetSecKit 信封加密 / AES + RSA 验签）为本项目
> **独立静态逆向 `libswsport.so` + Frida Hook 所得**；自抓包仅覆盖**读接口**，用于校准与验证。

> ⚠️ 上游项目采用 **CC BY-NC 4.0（署名-非商业性使用）** 许可，因此本工具同样
> **严禁任何商业用途与倒卖**，详见上方免责声明与 [LICENSE](LICENSE)。

---

## 📦 下载（推荐给普通用户）

不想折腾环境？直接下载打包好的成品 —— **Windows 与 Android 都有**：

| 平台 | 资产 | 说明 |
| --- | --- | --- |
| **Windows x64** | [`sw-campus-tool-win-x64.zip`](https://github.com/YYMY034/sw-campus-tool/releases/latest) | 绿色免安装，**内置 Python 运行时**，解压即用 |
| **Android** | [`sw-campus-mobile.zip`](https://github.com/YYMY034/sw-campus-tool/releases/latest) | Termux 一键安装包，**无需 root**，手机也能跑 |

### Windows

1. 解压到任意目录（路径避免特殊字符）
2. 双击 `Start.bat`
3. 浏览器自动打开 `http://127.0.0.1:8765`，输入手机号 + 密码即可使用

### Android（Termux）

```bash
# 1) 安装 Termux —— 请用 F-Droid 或 GitHub Releases 的 arm64-v8a 版
#    ⚠️ 不要用 Google Play 版（已停止维护）
# 2) 把 sw-campus-mobile.zip 传到手机「下载」目录，然后：
termux-setup-storage
pkg install -y unzip
cd ~/storage/downloads && unzip -o sw-campus-mobile.zip && cd sw-campus-mobile
bash install.sh     # 首次约 2~5 分钟，自动装好依赖
bash start.sh       # 自动打开浏览器；没开就手动访问 http://127.0.0.1:8765
```

遇到问题先跑 `bash check.sh`，会把环境信息一次性打印出来。

> **登录依赖**：登录需要 `numpy` + `Pillow` 来识别滑块验证码缺口。若点「登录」没反应，
> 执行 `pkg install -y python-numpy python-pillow` 后重开控制台即可。
> 也可以完全绕过登录 —— 把电脑上已登录的 `session.json`、`device_bind.json`、
> `devices.json`、`active_device.txt` **这 4 个文件**一起拷进手机 `app/` 目录
> （⚠️ 只拷 `session.json` 会因设备指纹不一致被风控拦截）。

> 想阅读源码或二次开发？见 [GitHub 仓库](https://github.com/YYMY034/sw-campus-tool)。

---

## 运行环境

- Python 3.10+
- 依赖：`pycryptodome`（信封加密 / RSA 验签）、`Pillow`、`numpy`（部分可视化）

```bash
pip install pycryptodome Pillow numpy
```

## 快速开始

### 可视化界面

```bash
python gui.py --port 8765
```

浏览器打开 `http://127.0.0.1:8765`，输入手机号 + 密码登录，即可在表单中
选择模式 / 距离 / 配速 / 设备，一键生成并提交跑步记录，界面右侧实时显示
运行日志。

### 命令行

```bash
# 登录（走 GT4 滑块 + 信封加密链）
python swcli.py login 手机号 密码

# 查看当前登录用户与设备身份
python swcli.py whoami

# 提交一条自由跑（自动生成轨迹 + 上传 OBS + 落库）
python swcli.py submit --mode free --dist 2.2 --dry-run

# 提交一条计分跑（需过打卡点，自动拉点）
python swcli.py submit --mode score --dist 2.2 --dry-run

# 查看跑步策略（policy / timestamp / minDistance）
python swcli.py policy

# 查看跑步数据概况（只读）
python swcli.py runwatch

# 设备档案管理
python swcli.py devices list
python swcli.py devices bind 手机号
```

## 模块简介

| 文件 | 说明 |
| --- | --- |
| `gui.py` | 可视化控制台（`http.server` 单页界面） |
| `swcli.py` | 命令行入口（登录 / 提交 / 自检 / 设备管理） |
| `swclient.py` | NetSecKit 信封加密客户端（AES + RSA 验签） |
| `swsubmit.py` | 提交体签名（31 字段 + roomId） |
| `swobs.py` | OBS 轨迹上传（GCJ-02 协议点，10 键结构） |
| `swmode.py` | 轨迹生成编排（自由跑 / 计分跑） |
| `campus.py` | 校区动态判定 |
| `auto_login.py` | 登录链（GT4 滑块 + 信封） |
| `run_all.py` | 批量 / 补跑 |
| `generator/run_gen.py` | 轨迹点生成入口 |
| `generator/rungen/` | 轨迹生成核心引擎（core / engine / route） |

## 自检

```bash
python swclient.py --selftest   # 信封加密 / RSA 验签闭环
python swsubmit.py  --selftest   # 提交签名向量
python swobs.py     --selftest   # OBS 协议点生成
```

--- 

## 说明与风控

- 本工具不修改手机端、不依赖手机，纯服务器接口交互。
- 校方会不定期更新 `runModePolicy` / 围栏 / 打卡点，若提交被拦，先确认
  校区坐标已收录（`campus.py` / `campus.json`）。
- 建议合理使用，避免同一天多次、超限与明显反常识的数据。

> 本仓库仅包含**源码**，不含任何账号数据、设备指纹档案或抓包材料。
