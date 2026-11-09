# Changelog

## 0.2.0 Preview — 2026-11-09

项目英文名：**Resume Optimization and Automated BOSS Applications**。

本版本按 MIT 许可证公开，供配置试用，不是稳定版。

### 已提供

- 从原简历直接匹配、先优化再匹配、无简历生成三条通用流程，配套空白模板与全虚构案例。
- 可配置的定时首次沟通模块；默认上午 09:30–11:30、下午 14:30–16:00，各随机启动一批，每批最多 50 次尝试、每天最多 100 次。
- 默认至少 31 秒间隔、单批 130 分钟发送时限；这些是可改策略，不是平台安全频率保证。
- 基础条件匹配、历史与疑似重复保护、配置绑定授权、独占运行、一次点击与同岗招呼回执核验、暂停和未知结果阻断。
- macOS Google Chrome 可见页面适配器，以及不访问浏览器、零发送的纯虚构演示。
- Python 标准库实现；94 项离线测试、白名单隐私检查与可复现导出。

### 首次使用

```sh
python3 scripts/workflow.py init my-search
python3 scripts/automation.py demo
python3 scripts/automation.py validate private/my-search
```

先阅读 `docs/07-automation.md`，再配置自己的私有账号核验、求职条件、浏览器窗口和页签。默认 `enabled=false`、`dry_run=true`，初始化不会开启真实发送。

### 已知限制

- 新浏览器适配器尚未完成真实在线投递验收，离线测试不证明当前平台界面已经可用。
- 内置真实浏览器适配器目前仅面向 macOS Google Chrome，Windows/Linux 暂无对应实现。
- 不代登录、不处理验证码、不发送附件、不更新简历、不自动安装系统任务；未知结果不重试。
- 复杂硬条件需要人工审核；不承诺语义岗位匹配、跨城市自动切换、回复率或录用。
- MIT 授权不包含第三方平台、用户私人材料或未获授权内容；平台规则、人工隐私复核及试用边界仍需分别确认。
- 尚未配置专用私密安全报告渠道；遵循 `SECURITY.md`，不要把私人材料、凭证或泄露证据上传公开 Issue。
