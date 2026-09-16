# 运动世界校园 · PC 端一键工具

> 一个零依赖（标准库 + pycryptodome）的 PC 端自动化工具，通过服务器接口
> 完成登录、跑步记录生成与提交。纯学习逆向 / 个人测试使用。

> ⚠️ **免责声明（务必阅读）**
>
> 1. **仅限学习研究**：本工具仅供学习逆向、协议研究与个人测试使用，**请勿用于作弊**或违反校园体育规定的行为。
> 2. **严禁倒卖**：本工具**完全免费**，严禁任何形式的**倒卖、转售、付费代刷、引流收费**或商业牟利。任何以本工具名义收费售卖的行为均与作者无关，属他人侵权行为。
> 3. **责任自负**：作者**不对使用本工具产生的任何直接或间接后果承担责任** —— 包括但不限于账号封禁、成绩作废、纪律处分、数据丢失、法律纠纷等，**一切后果由使用者自行承担**。
> 4. 请遵守学校及相关平台的使用条款。
> 5. 下载、安装或使用本工具，即视为已阅读并同意上述全部条款。
>
> 📄 完整法律条款详见 [LICENSE](LICENSE)。

---

## 📦 下载免安装版（推荐给普通用户）

不想折腾环境？直接下载**绿色免安装包**（内置 Python 运行时，解压即用）：

**➡️ [下载 sw-campus-tool-win-x64.zip](https://github.com/YYMY034/sw-campus-tool/releases/latest)**

1. 解压到任意目录（路径避免特殊字符）
2. 双击 `Start.bat`
3. 浏览器自动打开 `http://127.0.0.1:8765`，输入手机号 + 密码即可使用

> 本仓库为**源码版**，适合想阅读/二次开发的用户，见下方说明。

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
