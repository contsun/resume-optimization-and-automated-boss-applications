# 可配置的 BOSS 定时首次沟通

0.2.0 将已有个人自动化中的启动窗口、数量限制、发送间隔、独占运行、历史去重、回执核验和遇错停止规则重新实现为独立的 Python 模块。不复制个人账号、求职条件、历史授权、浏览器数据或 cc-connect 会话。它不是把旧工作目录直接打包，也不改变原任务。

**后续确实可以只改时间和数量；但首次使用还必须设置自己的账号、求职条件、浏览器入口，并明确授权。** 默认 `enabled=false`、`dry_run=true`，下载、初始化和查看计划都不会开启真实发送。

## 1. 默认执行标准

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `timezone` | `Asia/Shanghai` | 使用配置的时区，不使用电脑显示时区猜测 |
| `weekdays` | `[1,2,3,4,5,6,7]` | 周一至周日；可改成工作日 |
| `windows` | 上午 `09:30`–`11:30`；下午 `14:30`–`16:00` | 各窗口内随机选择一个启动分钟；右端时间不含在内 |
| `limits.per_batch` | `50` | 每批最多尝试的首次沟通数，不是保证成功数 |
| `limits.per_day` | `100` | 所有批次和已记录历史合并计算的当日尝试上限 |
| `limits.minimum_interval_seconds` | `31` | 所有已记录发送尝试之间的最短间隔 |
| `limits.maximum_run_minutes` | `130` | 单批发送时限；到本地午夜也必须停止新增点击，收尾核验可能稍晚完成 |
| `limits.maximum_candidates` | `200` | 单批最多读取多少个不同候选岗位 |
| `limits.maximum_expansions` | `10` | 最多扩展推荐列表多少次，避免无限滚动 |

这些只是可修改的执行策略，**不是平台允许自动化的证明，也不保证所谓安全频率**。数量不足时如实结束，不为了凑数放宽条件、处理验证码或绕过平台限制。一次点击可能产生在线资料可见性变化，不发送附件也不等于履历不可见。

启动时间按天保存，不在每次检查时重新随机。错过启动窗口不补跑；已结束、出错、进程崩溃留下的 `running` 批次不会被自动重开。当天计划一旦生成，修改自动化配置不重抽计划：新配置只能用于尚未生成计划的日期。修改配置或参考简历后，旧的真实发送授权失效，需要重新确认。

## 2. 初始化、零发送演示

在项目根目录执行，要求 Python 3.10+，代码只使用标准库：

```sh
python3 scripts/workflow.py init my-search
python3 scripts/automation.py demo
python3 scripts/automation.py validate private/my-search
python3 scripts/automation.py plan private/my-search
```

初始化会创建私有的 `profile.json` 与 `automation.json`。`demo` 只用临时虚构数据和演示适配器，不读取浏览器，输出 `planned`，`attempted` 与 `verified_greetings` 都为 0。演示状态与真实台账分开，不能冒充实际投递成功。

已有工作区没有 `automation.json` 时，手动将 `config/automation.example.json` 复制到该私有工作区；不要覆盖旧配置或复制其他人的私人资料。

## 3. 修改时间和数量

编辑 `private/my-search/automation.json`，不是修改脚本。比如每个工作日上午启动一批，每批最多 20、每天最多 20：

```json
{
  "weekdays": [1, 2, 3, 4, 5],
  "windows": [{"name": "morning", "start": "10:00", "end": "10:30"}],
  "limits": {
    "per_batch": 20,
    "per_day": 20,
    "minimum_interval_seconds": 31,
    "maximum_run_minutes": 60,
    "maximum_candidates": 100,
    "maximum_expansions": 5
  }
}
```

这只是需要替换的字段片段，保留完整配置中的其余字段。支持增加或删除命名窗口，不允许重叠、跨午夜或倒置窗口。批次上限与每日上限分别执行；`profile.outreach.max_new_conversations` 若另有更小上限，也取较小值。配额统计发送尝试，即使没有拿到成功回执，也不能返还预算重新点击。

## 4. 首次还需确认求职条件

在私有 `profile.json` 中填写：

- `target_roles`、`cities`：非空数组；目前按岗位标题和页面地点的明确文字匹配，不做语义推断。
- `excluded_roles`、`excluded_employers`：明确排除项。
- `salary.minimum`、`maximum`：人民币月薪，单位元；上限可为空。
- `salary.matching_rule`：`overlap`（区间交集）、`minimum_at_least`（岗位起薪达到下限）、`minimum_within_range`（岗位起薪处于指定区间）。
- `resume_version`：已确认的参考简历版本；`outreach.action="first_greeting"`、`outreach.send_attachment=false`。
- 首次沟通及定时范围明确确认后，才设置 `external_send_enabled=true`、`schedule_enabled=true`。

目前只自动比较页面可读的 `15-20K` 一类月薪范围；不猜测字体编码，不自动换算日薪、年包、税后薪资或面议。不能读清则跳过并记录原因。默认空条件不是无限制，真实执行缺少必要条件会拒绝授权。

`automation.json` 中还可设置 `matching.required_keywords`、`excluded_keywords`、`allowed_education` 和 `employer_aliases`。别名统一到一个雇主键；同雇主、同方向、同城市或同招聘人的疑似重复保守跳过，不声称已证明岗位独立。

复杂硬条件、职级和工作方式不会被简单关键词匹配冒充已经核验。`profile.hard_requirements`、`target_levels`、`work_modes` 非空，或 `matching.require_manual_review=true` 时，需要 `reviews.json` 中与当前详情指纹和参考版本一致的人工批准：

```json
[
  {
    "fingerprint": "从私有预演台账复制的岗位指纹",
    "resume_version": "自己确认的参考版本",
    "approved": true,
    "reason": "本人逐项核验硬条件后的依据"
  }
]
```

预演需先让本人打开目标页面，正确配置浏览器，保持 `dry_run=true` 后设置 `enabled=true`，在启动窗口运行 `tick`。为准备人工审核而未通过的岗位，其详情与指纹记录在私有台账；不要贴到公开 Issue。软偏好和行业偏好不自动变成硬条件，本版本也不承诺智能岗位排序、自动搜索关键词或跨城市切换。

## 5. 浏览器适配与明确授权

内置适配器目前仅实现 **macOS Google Chrome** 的可见页面访问，不依赖原作者的浏览器、模型、机器人账号或附件目录。Windows/Linux 可以使用离线工具及调度逻辑，但本版本未提供它们的真实浏览器适配器，也未进行实机验证。

1. 本人检查使用时的平台规则，再在自己的 Chrome 中打开 BOSS 岗位页并登录、选择本次城市和筛选条件。脚本不会登录、打开账号、索要密码或复制 Cookie。
2. 本人决定是否允许本机终端控制 Chrome、是否开启 Chrome 的 Apple Events JavaScript 支持；工具不会替你调整系统权限。未允许时会停止。
3. 在 `automation.browser` 填写本人的 `account_name`、`window_id`、`tab_id`，只操作这一页签。可以在自己明确允许的终端执行下列只读命令确认 ID；不要使用其他助手的历史 ID：

```sh
osascript -e 'tell application "Google Chrome" to get id of every window'
osascript -e 'tell application "Google Chrome" to get id of every tab of window id 1'
```

第二条中的 `1` 需替换为你刚核对的窗口 ID。这些命令应由本人在已打开的浏览器上使用，不由项目初始化或演示自动运行。

4. 检查预演、求职条件、历史台账与数量后，将 `automation.enabled=true`、`automation.dry_run=false`。
5. 用明确的截止日期确认本工作区的定时首次沟通授权，日期需替换为本人选择的实际日期：

```sh
python3 scripts/automation.py validate private/my-search
python3 scripts/automation.py authorize private/my-search --until 2026-12-01 --confirm-first-greeting
python3 scripts/automation.py serve private/my-search
```

`authorize` 只保存本地确认，不联系招聘方。授权绑定当前账号、时间与数量、求职条件、参考版本和通知设置；改动任一配置会失效。`serve` 才是持续的调度进程，每分钟检查一次，到点执行；也可以由使用者自行配置系统定时器调用 `tick`。项目不自动安装系统任务、不改任何已有定时任务。电脑关机、休眠或程序退出时不能保证执行，错过窗口不自动补跑。

页面选择器来自已有可见页面流程的通用化版本，但 **0.2.0 没有用真实账号做新适配器的在线发送验收**。界面变更、薪资读不清、弹窗、登录或账号变化、验证码与平台限制都会停止或跳过，不能把离线测试通过说成现行平台已验证可用。

## 6. 暂停、核验与私有记录

```sh
python3 scripts/automation.py status private/my-search
python3 scripts/automation.py pause private/my-search
```

暂停请求不等待整个批次的文件锁；执行器在下一次前置检查发现后停止。已经交给浏览器的点击不能撤销，仍须按原尝试核验。恢复需要重新明确授权，不重开旧的关闭批次，也不自动补足额度。

发送前先将尝试写入私有追加台账并落盘，再最多点击一次「立即沟通」。只有当前岗位与招呼内容相符的可见成功回执才计入 `verified_greetings`；不发送附件、不追加消息、不修改在线简历或平台设置。窗口和页签 ID、账号、岗位标题、公司、地点、薪资、招聘人、详情和截止时间均在发送前复核。

状态、授权、人工审核及 JSONL 台账都保存在私有工作区；人工 `applications.csv` 中已确认的历史和待核实状态也会参与保护。人工岗位键应使用与页面一致的本站相对岗位路径，雇主与方向键也应保持一致；无法可靠对齐的旧历史应先人工归一，不应空台账直接恢复一个曾大量投递的账号。项目不会自动扫描其他工作区或导入作者的历史。

未知发送结果会阻断以后所有批次，不因退出、重启或跨日而消失。由本人查看原会话证据后，才可追加结论，不能为了恢复而猜测成功或未发送：

```sh
python3 scripts/automation.py resolve private/my-search --attempt-id ACTUAL_ATTEMPT_ID --outcome verified_no_send --evidence "本人核验的私有证据引用" --confirm-reviewed
```

`resolve` 不点击、不覆盖旧记录，也不重开已关闭批次。真实结果未明时不要运行它。

可选通知使用 `notification.command` 参数数组调用使用者自己的可信本地通知程序，以 JSON 从标准输入传入批次汇总，不经过 shell；默认禁用，不绑定飞书机器人或组织。命令退出成功只记录 `command_succeeded`，**不冒称消息已送达**；实际送达要由所选通道回执确认。通知失败不重跑投递批次。

## 7. 验证范围

- 已有纯虚构的计划、数量、间隔、授权失效、去重、未知回执、暂停、超时与浏览器协议测试；测试不联系招聘方。
- 未导入或运行个人现有定时任务；未读取浏览器登录态，未发送真实招呼或附件。
- 新适配器未完成真实平台验收、Windows/Linux 浏览器支持、平台自动化许可或投递效果验证。
- MIT 许可不等于第三方平台许可；发布与使用仍须分别确认隐私、授权和平台边界。
