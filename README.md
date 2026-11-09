# Resume Optimization and Automated BOSS Applications

**简历优化与 BOSS 直聘自动投递**

整理简历、筛选岗位，再按你设定的时间和数量发起沟通。你可以先优化简历，也可以直接使用已有简历开始找工作；没有简历时，可以从工作、项目或实习经历开始整理。

## 可以做什么

| 功能 | 使用方式 |
| --- | --- |
| 优化已有简历 | 配合你常用的 AI 助手，梳理经历、改进表达，突出与目标岗位相关的成果 |
| 从零整理简历 | 按模板补充工作、项目和教育经历，逐步形成简历正文 |
| 筛选 BOSS 岗位 | 按岗位名称、城市、月薪、关键词和排除条件筛选浏览器中的岗位 |
| 定时自动投递 | 设置每天的启动时间、每批数量和每日上限，由本地程序执行 |
| 记录求职进度 | 保存沟通记录，按历史记录跳过重复岗位，查看投递、回复和面试进展 |

这里的「自动投递」指在 BOSS 直聘中点击 **「立即沟通」**，发起首次招呼。工具不会修改平台简历或自动发送附件；需要发送附件简历时，在 BOSS 中手动操作即可。回复和面试进度也需要自行记入求职记录。

## 使用前准备

- **只整理或优化简历**：准备一份简历或经历说明，以及你常用的 AI 助手，无需安装脚本。
- **使用本地工具**：安装 Python 3.10 或更高版本。脚本只使用 Python 标准库，无需额外安装依赖。
- **使用自动投递**：使用 macOS 和 Google Chrome，并在自己的浏览器中登录 BOSS 直聘。

项目通过命令行运行，没有单独的网页或桌面界面。简历优化使用项目提供的提示词和模板，不是脚本自动改写简历。

## 只想先优化简历

1. 打开 [简历整理提示词](prompts/resume-intake.md)，将内容复制给你常用的 AI 助手。
2. 提供简历文字或助手能读取的文件，并说明想找的岗位；有目标职位描述时可以一起提供。
3. 告诉助手你想先优化简历，还是保留原文、直接分析适合的岗位。

例如：

> 这是我的简历，我想找产品经理岗位。请先帮我看看哪些经历值得突出，再修改结构和表达。缺少信息时先问我，不要编造经历和成果数字。

没有现成简历也可以开始：提供几段工作或项目经历，或者参考 [个人信息模板](templates/intake.md) 整理。具体写法见 [简历优化教程](docs/01-resume-optimization.md)。

## 开始自动投递

### 1. 下载项目，创建自己的工作目录

```sh
git clone https://github.com/contsun/resume-optimization-and-automated-boss-applications.git
cd resume-optimization-and-automated-boss-applications
python3 scripts/workflow.py init my-search
```

也可以在 GitHub 项目页选择 **Code → Download ZIP**，解压后在项目目录运行最后一条命令。

初始化后，你的配置和求职材料放在 `private/my-search/`：

| 文件 | 用途 |
| --- | --- |
| `profile.json` | 想找的岗位、城市、薪资和简历版本 |
| `automation.json` | 投递时间、数量、间隔和浏览器设置 |
| `resume.md` | 简历正文，也可以另存自己的简历文件 |
| `applications.csv` | 沟通、回复、面试等求职记录 |

### 2. 填写求职条件

打开 `private/my-search/profile.json`，修改下列字段，保留文件中的其他字段：

| 字段 | 填写示例 | 说明 |
| --- | --- | --- |
| `target_roles` | `["产品经理", "产品运营"]` | 岗位名称中包含的文字 |
| `cities` | `["上海"]` | 希望工作的城市 |
| `salary.minimum` | `15000` | 最低月薪，单位为人民币元 |
| `salary.maximum` | `25000` | 最高月薪；不设上限时填 `null` |
| `salary.matching_rule` | `"overlap"` | 岗位薪资与期望范围有交集即可入选 |
| `resume_version` | `"resume-2026-11"` | 给本次使用的简历起一个版本名 |
| `outreach.action` | `"first_greeting"` | 发起首次沟通 |
| `outreach.send_attachment` | `false` | 不发送附件简历 |
| `external_send_enabled` | `true` | 允许按配置发起沟通 |
| `schedule_enabled` | `true` | 允许定时执行 |

表中的岗位、城市和薪资只是填写示例，请换成自己的条件。可以用 `excluded_roles` 排除岗位、用 `excluded_employers` 排除公司；关键词设置见 [自动投递配置](docs/07-automation.md)。

### 3. 设置投递时间和数量

打开 `private/my-search/automation.json`。日常调整时间和数量只需修改这个文件，不用改 Python 脚本。

| 设置 | 配置字段 | 默认值 |
| --- | --- | --- |
| 执行日期 | `weekdays` | 周一至周日；数字 `1` 至 `7` 分别表示周一至周日 |
| 启动时间段 | `windows` | 上午 09:30–11:30、下午 14:30–16:00，每个时间段随机启动一批 |
| 每批数量上限 | `limits.per_batch` | 最多尝试 50 次首次沟通 |
| 每日数量上限 | `limits.per_day` | 最多尝试 100 次首次沟通 |
| 沟通间隔 | `limits.minimum_interval_seconds` | 至少 31 秒 |
| 单批运行时限 | `limits.maximum_run_minutes` | 最长 130 分钟，到午夜也会停止新增沟通 |

例如，想改成「只在工作日上午 10:00–10:30 启动，每天最多尝试 20 次」，修改对应字段为：

```json
{
  "weekdays": [1, 2, 3, 4, 5],
  "windows": [
    {"name": "morning", "start": "10:00", "end": "10:30"}
  ],
  "limits": {
    "per_batch": 20,
    "per_day": 20
  }
}
```

上面是需要修改的字段，不是整份配置，请保留 `limits` 中的其他设置。时间使用 `timezone` 指定的时区，默认是北京时间；启动时间段不包含结束时刻。数量按尝试次数计算，不是成功沟通数或招聘方回复数。

### 4. 连接自己的 Chrome 浏览器

1. 在 Chrome 中打开 BOSS 直聘的岗位列表页，登录自己的账号，选好本次的城市和筛选条件。程序从当前页面读取岗位，不会自动切换城市。
2. 开启 Chrome 的 Apple Events JavaScript 选项；macOS 提示是否允许终端控制 Chrome 时，选择允许。
3. 在终端查看浏览器窗口和标签页编号：

```sh
osascript -e 'tell application "Google Chrome" to get id of every window'
osascript -e 'tell application "Google Chrome" to get id of every tab of window id 1'
```

第二条命令中的 `1` 要换成第一条查到的目标窗口编号。将 BOSS 岗位页所在的窗口编号、标签页编号，以及页面显示的账号名称，填入 `automation.json` 的以下字段：

- `browser.window_id`：窗口编号。
- `browser.tab_id`：标签页编号。
- `browser.account_name`：当前 BOSS 账号的显示名称。

### 5. 开启自动投递

初始化后的配置不会发送消息。准备开始时，在 `automation.json` 中将 `enabled` 改为 `true`、`dry_run` 改为 `false`。

在项目目录运行下面的命令。示例将本次自动投递的截止日期设为 7 天后：

```sh
python3 scripts/automation.py validate private/my-search
END_DATE=$(python3 -c 'from datetime import date, timedelta; print(date.today() + timedelta(days=7))')
python3 scripts/automation.py authorize private/my-search --until "$END_DATE" --confirm-first-greeting
python3 scripts/automation.py serve private/my-search
```

`authorize` 保存本次投递设置，`serve` 启动定时程序，每分钟检查一次计划，到设定时间执行。想设置其他截止日期，可以将 `"$END_DATE"` 换成 `YYYY-MM-DD` 格式的日期，范围为当天至未来一年内。

运行期间保持电脑唤醒、Chrome 登录和终端程序运行。电脑休眠或程序退出后，错过的时间段不会补跑。

更改求职条件、时间、数量、浏览器或简历版本后，重新运行授权命令，再启动程序。已经生成的当天计划不会重新抽取启动时间，新时间设置用于尚未生成计划的日期。

## 查看进度和暂停

查看自动投递状态：

```sh
python3 scripts/automation.py status private/my-search
```

暂停后续投递：

```sh
python3 scripts/automation.py pause private/my-search
```

暂停命令会让执行中的程序在下一次操作检查时停止。重新授权后可以恢复后续投递；在运行窗口按 `Ctrl+C` 可以退出定时程序。

求职记录保存在 `private/my-search/`。手动记录的沟通、回复和面试进展，可以用下面的命令汇总：

```sh
python3 scripts/workflow.py summary private/my-search/applications.csv
```

想先了解程序输出，可以运行演示；它使用虚构岗位，不读取浏览器，也不会发送消息：

```sh
python3 scripts/automation.py demo
```

## 常见问题

**必须先优化简历，才能自动投递吗？**

不需要。可以直接使用现有简历，填好求职条件后配置自动投递。

**Windows 和 Linux 可以用吗？**

可以使用简历模板、提示词和本地记录工具。自动操作 BOSS 浏览器的部分需要 macOS 和 Google Chrome。

**后续只改投递时间和数量就行吗？**

账号、浏览器和求职条件配置好后，日常只需修改 `automation.json` 中对应的时间和数量，并重新授权。浏览器窗口或标签页编号变化时，也要更新对应设置。

**自己的简历和配置会上传到 GitHub 吗？**

初始化生成的文件保存在本机的 `private/` 目录，已在 `.gitignore` 中排除，不会随普通 Git 提交上传。不要强制提交这个目录，也不要在公开 Issue 中粘贴简历、密码、验证码或 Cookie。使用 AI 助手整理简历时，材料由你选择的服务处理，建议先去掉无关的个人和公司敏感信息。

## 更多说明

- [第一次使用](docs/00-start-here.md)：有简历、没有简历时分别怎么开始。
- [简历优化](docs/01-resume-optimization.md)：梳理经历、改写表达、按目标岗位调整。
- [BOSS 投递操作](docs/02-boss-application.md)：平台资料、首次沟通与附件发送。
- [自动投递配置](docs/07-automation.md)：完整配置、调度、暂停和记录说明。
- [使用示例](docs/06-walkthroughs.md)：从整理简历到开始找工作的对话示例。

## 开源许可

本项目采用 [MIT 许可证](LICENSE)。欢迎使用、修改和贡献；参与开发请阅读 [贡献指南](CONTRIBUTING.md)，反馈隐私或安全问题请阅读 [安全说明](SECURITY.md)。
