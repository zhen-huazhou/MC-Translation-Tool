# Minecraft 模组自动汉化工具

一键扫描 Minecraft 整合包中的模组语言文件与 FTB Quests 任务书，自动翻译并生成可安装的简体中文补丁。

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-1.2.1-orange.svg)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 项目简介

本工具用于快速生成整合包汉化补丁。它会读取 `mods` 目录中的 JAR 文件，提取尚未提供 `zh_cn.json` 或中文条目不完整的模组语言文本；同时可自动发现 `config/ftbquests/quests` 中的任务标题、章节标题和描述，调用 AI、百度翻译或 DeepL 完成翻译。工具会按任务书内容自动判断 FTB Quests 的存储类型：本地化键会合并到模组资源包；内联文本或外部语言文件会额外生成可替换的完整 quests 目录。

## 使用声明

本项目为开源、免费工具，仅供个人使用、学习与研究，禁止将本项目或其修改版本用于商业用途，包括但不限于出售软件、付费代做、捆绑收费服务，或将其作为商业产品的一部分。

本项目按“现状”提供，不保证翻译结果完全准确，也不对使用过程中产生的 API 费用、数据丢失或其他损失承担责任。使用前请自行备份重要文件及配置。

> 项目当前使用 MIT License。上述内容用于说明项目定位和作者意愿；商业合作或授权事宜，请先联系项目作者。若需要严格限制商业用途，应将仓库许可证同步调整为相应的非商业许可证。

## 主要功能

- 支持 GUI 和 CLI 两种运行方式。
- 支持 AI API（DeepSeek/OpenAI 兼容接口）、百度翻译和 DeepL。
- 自动扫描 Forge、Fabric 等模组，无需完整解压 JAR。
- 自动检测 FTB Quests 内联文本、本地化键、外部语言文件及混合模式。
- 模组与 FTB Quests 任务书可单独汉化，也可组合处理；未提供任何数据源时禁用开始按钮。
- 本地化键直接合并到资源包；内联式任务书生成完整 quests 目录，并可一键备份替换。
- 自动区分完整汉化、部分汉化和无中文模组；部分汉化只补全缺失或空值条目，并保留已有译文。
- 可选择强制重新翻译全部英文条目；已有中文任务书译文仍会保留。
- 翻译结果按接口、模型和配置隔离缓存，避免重复请求和费用。
- 自动去重、批量请求并支持配置并发数。
- 根据 MC 版本自动选择资源包 `pack_format`。
- 安全取消任务，避免生成不完整的资源包。
- 可打包为单文件 EXE，无需目标电脑安装 Python。

## 快速开始

### 使用发布版 EXE

1. 解压发布包。
2. 双击 `MC_Hanhua_Tool.exe`。
3. 选择 `mods` 文件夹、MC 版本、翻译接口并填写 API Key；如未手动填写 FTB Quests 目录，工具会自动从 `mods` 同级目录检测。
4. 选择输出路径，点击“开始汉化”。
5. 将生成的资源包导入 HMCL、MultiMC 等启动器；如果提示生成了完整 quests 目录，请按下方说明手动替换，或提前勾选“一键导入”。

EXE 通常可以单独分享；首次运行会在当前目录生成 `user_config.json` 和 `cache/`。建议放在有写入权限的普通文件夹中运行。

### 从源码运行

需要 Python 3.8 或更高版本。

```powershell
pip install -r requirements.txt
python src/main.py
```

Windows 下也可以双击 `tools\快速开始.bat`，脚本会自动检查并安装缺少的依赖。

### 命令行模式

```powershell
python src/main.py --cli `
  --mods "D:\HMCL\mods" `
  --output "汉化补丁.zip" `
  --ftb-quests "D:\HMCL\.minecraft\config\ftbquests\quests" `
  --api-type ai `
  --api-key "你的 API Key" `
  --batch-size 10 `
  --max-workers 3
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--mods` | 模组文件夹路径；可与 `--ftb-quests` 单独或同时使用 |
| `--output` | 输出资源包路径 |
| `--api-type` | `ai`、`baidu` 或 `deepl` |
| `--api-key` | 覆盖配置文件中的 API Key |
| `--pack-format` | 手动指定资源包格式 |
| `--batch-size` | 每批翻译条目数 |
| `--max-workers` | 并发请求数 |
| `--force` | 忽略完整汉化跳过逻辑，强制重新翻译全部英文条目 |
| `--ftb-quests` | FTB Quests 的 `quests` 目录；指定 `--mods` 时默认自动检测，也可单独使用 |
| `--ftb-output` | 指定完整 quests 汉化目录的输出根目录 |
| `--ftb-auto-import` | 自动备份并替换内联/外部语言模式的 quests 目录 |
| `--no-ftb-quests` | 禁用 FTB Quests 任务书汉化 |
| `--config` | 指定配置文件，默认 `user_config.json` |


## FTB Quests 任务书汉化

FTB Quests 的任务数据位于实例根目录的 `config/ftbquests/quests`。1.20 及以前通常使用内联文本，较新的分支可能使用本地化键或外部语言文件；实际分界还受 FTB Quests 分支和整合包转换情况影响，因此工具不会只按版本猜测，而是直接分析任务书内容并自动分类：

### 1. 本地化键模式

章节 SNBT 中的标题或描述类似：

```snbt
title: "{quests.example.title}"
description: ["{quests.example.desc}"]
```

这类文本会交给 Minecraft 语言系统解析。工具会读取 `config/ftbquests/quests/lang/en_us.snbt` 或整合包 `resourcepacks` 中的英文语言文件，生成 `assets/ftbquests/lang/zh_cn.json`，并直接合并到模组汉化资源包中。用户只需导入这一个资源包，不需要替换 quests。

### 2. 内联式 SNBT

章节 SNBT 中直接保存英文文本：

```snbt
title: "Getting Started"
description: ["Welcome to the pack"]
```

这类文本无法被资源包覆盖，因此会产生两份产物：

1. 模组汉化资源包。
2. 主输出文件旁的 `*_FTBQuests/quests` 完整目录。

工具默认直接生成目录而不是压缩包，因为目录复制不需要压缩和解压，速度更快，也更适合一键替换。

未勾选一键导入时，手动操作步骤：

1. 备份原 `config/ftbquests/quests` 目录。
2. 用生成的 `*_FTBQuests/quests` 完整替换原目录。
3. 启动游戏检查任务书文本。

勾选“一键导入”或 CLI 使用 `--ftb-auto-import` 后，工具会自动执行：

1. 将原 `quests` 重命名为 `quests_backup`。
2. 如果该备份已经存在，则使用带时间戳的 `quests_backup_yyyyMMdd_HHmmss`。
3. 将生成的完整 `quests` 目录复制到原位置。
4. 如果复制失败，会自动回滚原目录。

### 3. 外部语言文件模式

新版 FTB Quests 会把任务文本放在 `config/ftbquests/quests/lang/en_us` 或 `lang/en_us.snbt` 中。这类数据也不能由资源包覆盖，工具会按内联式模式生成完整 `quests` 目录，并在其中写入对应的 `zh_cn` 语言文件。

混合模式会同时处理：本地化键写入资源包，内联或外部语言文本写入完整 quests 目录。已有中文条目会保留，再次运行时可继续补齐。

## API 配置

### AI API（推荐）

- 接口地址：`https://api.deepseek.com/v1`
- 默认模型：`deepseek-chat`
- 获取地址：<https://platform.deepseek.com/>

兼容 OpenAI 格式的接口也可以修改 `api_configs.ai.base_url` 和 `model` 后使用。

### 百度翻译

- 需要 APP ID 和 Secret Key。
- 获取地址：<https://api.fanyi.baidu.com/>

### DeepL

- 需要 Auth Key。
- 获取地址：<https://www.deepl.com/pro-api>

API Key 可以通过界面保存到 `user_config.json`。该文件包含敏感信息，不应公开分享。

## 缓存与配置

运行时生成的文件：

| 文件或目录 | 用途 |
| --- | --- |
| `user_config.json` | 保存界面配置和 API Key |
| `cache/translation_cache.json` | 保存翻译缓存 |

“使用翻译缓存”“智能补全部分汉化的模组，完整汉化仍跳过”和“自动检测 FTB Quests 目录”位于高级设置页。自动检测默认开启；关闭后如果 FTB Quests 路径为空，将不执行任务书汉化。智能补全默认只跳过完整汉化模组；取消勾选后会忽略已有中文并强制重新翻译全部英文条目，同时保留中文文件中的额外键。

## MC 版本与 pack_format

| Minecraft 版本 | pack_format |
| --- | ---: |
| 1.21.4 | 46 |
| 1.21.2 - 1.21.3 | 42 |
| 1.20.5 - 1.21.1 | 34 |
| 1.20.3 - 1.20.4 | 22 |
| 1.20 - 1.20.2 | 15 |
| 1.19.4 | 13 |
| 1.19.3 | 12 |
| 1.19 - 1.19.2 | 9 |
| 1.18 - 1.18.2 | 8 |
| 1.17 - 1.17.1 | 7 |
| 1.16.2 - 1.16.5 | 6 |

列表中没有的版本可以在界面中选择“其他/手动设置”，或通过 `--pack-format` 指定。

## 项目结构

```text
mc_hanhua/
├── src/
│   ├── __init__.py          # 包信息
│   ├── main.py              # GUI/CLI 入口
│   ├── gui.py               # tkinter 图形界面
│   ├── mod_scanner.py       # 模组扫描与语言文件提取
│   ├── translator.py        # AI/百度/DeepL 翻译与缓存
│   ├── ftbquests.py         # FTB Quests 扫描与补丁生成
│   └── resourcepack.py      # 资源包生成与验证
├── tools/
│   ├── 快速开始.bat         # 检查依赖并启动程序
│   ├── 测试运行.bat         # 编译与导入冒烟测试
│   └── build.bat            # 使用 PyInstaller 打包
├── dist/                    # 打包及发布产物
├── config_template.json     # 默认配置模板
├── create_release.py        # 生成发布 ZIP
├── MC_Hanhua_Tool.spec      # PyInstaller 配置
├── requirements.txt         # Python 依赖
├── README.md                # 项目说明
├── CHANGELOG.md             # 更新日志
└── LICENSE                  # MIT 许可证
```

## 开发与打包

技术栈：Python、tkinter、requests、PyInstaller，以及 `json`、`zipfile`、`threading` 等标准库。

运行源码或冒烟测试：

```powershell
python src/main.py
tools\测试运行.bat
```

构建单文件 EXE：

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m PyInstaller --noconfirm --clean MC_Hanhua_Tool.spec
```

也可以双击 `tools\build.bat`。生成文件位于 `dist\MC_Hanhua_Tool.exe`。

生成发布 ZIP：

```powershell
python create_release.py
```

发布包包含 EXE、README、配置模板和许可证。

## 常见问题

- 扫描不到模组：确认选择的是包含 `.jar` 文件的 `mods` 目录。
- 部分已有中文的模组也被处理：这是正常行为，工具会只补全相对英文文件缺失或为空的键，并保留已有中文译文。
- 翻译失败：检查 API Key、余额、网络连接和服务地址。
- 资源包无效：确认 MC 版本与 `pack_format` 对应。
- FTB Quests 未汉化：确认任务目录位于 `config/ftbquests/quests`，或通过界面/`--ftb-quests` 手动指定。
- FTB Quests 补丁不生效：先看日志中的检测模式。内联式必须替换 config/ftbquests/quests，本地化键模式才可以直接用资源包。
- 一键导入失败：确认整合包实例可写，并检查日志中的回滚和备份路径。
- 翻译速度慢：首次翻译需要请求接口，后续启用缓存会明显加快。
- Windows 提示未知发布者：EXE 未进行商业代码签名，可选择“更多信息”后继续运行。

## Bug、漏洞与问题反馈

如果使用过程中遇到 Bug、翻译异常、兼容性问题或有功能建议，可以通过本项目托管仓库的 **Issues / 问题反馈** 页面提交。GitHub、Gitee 等平台的操作入口名称可能略有不同，请以实际项目仓库为准。

### 提交 Bug 时请尽量提供

1. 工具版本，例如 `v1.2.1`。
2. Windows 版本及是否为 64 位系统。
3. 使用的运行方式：EXE、源码运行或 CLI。
4. Minecraft 版本、整合包名称及使用的翻译接口。
5. 可复现问题的详细步骤。
6. 预期结果和实际结果。
7. 完整错误日志、截图或报错信息。

请先搜索已有 Issue，避免重复提交。提交时不要公开 API Key、`user_config.json`、个人路径、账号信息或其他隐私数据。

### 安全漏洞反馈

如果发现 API Key 泄露、任意文件读写、远程代码执行等安全漏洞，请不要直接在公开 Issue 中披露完整利用细节或敏感信息。若托管平台支持，请使用 **Security / Private vulnerability reporting / 私密漏洞报告** 功能提交；如果不支持私密报告，请先提交一条不含漏洞细节和密钥的问题，说明需要作者提供私密沟通方式。

作者会尽力处理反馈，但无法承诺固定的修复时间。提交清晰、可复现的信息，可以显著提高问题处理效率。

## 支持项目

制作和维护这个项目需要投入不少时间。如果本项目对你有帮助，欢迎在项目仓库点一个 **Star**，这是对作者持续维护和更新最好的鼓励。也欢迎将问题、建议或改进想法通过 Issues 反馈。

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
