from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime


class BrowserError(RuntimeError):
    pass


def valid_job_path(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"/job_detail/[A-Za-z0-9_~-]+\.html", value) is not None


class ChromeBossBrowser:
    def __init__(self, settings: dict, sleep=time.sleep):
        self.settings = settings
        self.sleep = sleep

    def evaluate(self, source: str):
        if sys.platform != "darwin":
            raise BrowserError("内置浏览器适配器目前仅支持 macOS Google Chrome")
        window_id = self.settings.get("window_id")
        tab_id = self.settings.get("tab_id")
        if any(type(value) is not int or value <= 0 for value in (window_id, tab_id)):
            raise BrowserError("先确认自己已登录的 Chrome 窗口和页签 ID")
        wrapped = (
            "(() => {try {"
            "if(location.protocol!=='https:'||location.hostname!=='www.zhipin.com'||location.pathname!=='/web/geek/jobs')"
            "throw new Error('Unexpected page');"
            "const data=(()=>{" + source + "})();return JSON.stringify({ok:true,data});"
            "}catch(error){return JSON.stringify({ok:false});}})()"
        )
        script = (
            "ObjC.import('ScriptingBridge');ObjC.import('Foundation');ObjC.import('AppKit');"
            "const applications=$.NSRunningApplication.runningApplicationsWithBundleIdentifier('com.google.Chrome');"
            "if(applications.count!==1)throw new Error('Open one Chrome application first');"
            "const browser=$.SBApplication.applicationWithProcessIdentifier(applications.objectAtIndex(0).processIdentifier);"
            "browser.timeout=12;"
            f"const tab=browser.valueForKey('windows').objectWithID({window_id})"
            f".valueForKey('tabs').objectWithID({tab_id});"
            "ObjC.unwrap(tab.performSelectorWithObject($.NSSelectorFromString('executeJavascript:'),"
            f"$({json.dumps(wrapped, ensure_ascii=False)})));"
        )
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript"], input=script, text=True,
                capture_output=True, timeout=20, check=True,
            )
            response = json.loads(result.stdout)
            if response.get("ok") is not True:
                raise BrowserError("页面结构、账号、岗位或操作前置条件发生变化，已停止")
            return response["data"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
            raise BrowserError("浏览器执行结果不明；不自动重试点击") from error

    def preflight(self) -> None:
        account = json.dumps(self.settings["account_name"], ensure_ascii=False)
        result = self.evaluate(
            "const visible=element=>element.getClientRects().length&&getComputedStyle(element).visibility!=='hidden';"
            f"return {{account:[...document.querySelectorAll('.label-text')].some(element=>element.innerText.trim()==={account}),"
            "restricted:/访问异常|操作频繁|安全验证|验证码|拖动滑块|开聊次数.*用完|今日.*上限/.test(document.body.innerText),"
            "dialogs:[...document.querySelectorAll('[role=dialog],.dialog-wrap,.dialog-container,.greet-boss-container')].some(visible)};"
        )
        if result != {"account": True, "restricted": False, "dialogs": False}:
            raise BrowserError("登录账号、平台限制或弹窗检查未通过；由本人处理后重新确认")

    def cards(self) -> list[dict]:
        return self.evaluate(
            "return [...document.querySelectorAll('.job-card-box')].map(element=>({"
            "job_key:element.querySelector('.job-name')?.getAttribute('href')})).filter(item=>item.job_key);"
        )

    def inspect(self, card: dict) -> dict:
        job_key = card.get("job_key")
        if not valid_job_path(job_key):
            raise BrowserError("岗位链接不是已观察到的本站相对职位链接")
        encoded = json.dumps(job_key)
        self.evaluate(
            "const anchor=[...document.querySelectorAll('.job-card-box .job-name')]"
            f".find(element=>element.getAttribute('href')==={encoded});"
            "if(!anchor)throw new Error('Missing card');"
            "anchor.closest('.job-card-box').scrollIntoView({block:'nearest'});"
            "anchor.closest('.job-card-box').click();return true;"
        )
        for attempt in range(12):
            self.sleep(0.4)
            detail = self.evaluate(
                "const root=document.querySelector('.job-detail-container');"
                "const selected=document.querySelector('.job-card-wrap.active');"
                "const header=root?.querySelector('.job-detail-info')?.innerText||'';"
                "return {job_key:selected?.querySelector('.job-name')?.getAttribute('href'),"
                "title:root?.querySelector('.job-detail-info .job-name')?.innerText.trim(),"
                "company:selected?.querySelector('.boss-name')?.innerText.trim(),"
                "location:selected?.querySelector('.company-location')?.innerText.trim(),"
                "salary:root?.querySelector('.job-salary')?.innerText.trim(),"
                "recruiter:root?.querySelector('.job-boss-info .name')?.innerText.trim(),"
                "address:root?.querySelector('.job-address-desc')?.innerText.trim(),"
                "education:header.match(/学历不限|初中及以下|高中|中专|中技|大专|本科|硕士|博士/)?.[0],"
                "text:root?.innerText,button:root?.querySelector('.op-btn-chat')?.innerText.trim(),"
                "button_key:root?.querySelector('.op-btn-chat')?.getAttribute('ka')};"
            )
            slug = job_key.rsplit("/", 1)[1].removesuffix(".html")
            if (
                detail.get("job_key") == job_key and detail.get("title")
                and "职位描述" in (detail.get("text") or "")
                and slug in (detail.get("button_key") or "")
            ):
                return detail
        raise BrowserError("岗位详情尚未完整或不能证明按钮对应当前岗位；停止而非猜测")

    def click(self, candidate: dict, deadline: datetime) -> None:
        expected = json.dumps(candidate, ensure_ascii=False)
        account = json.dumps(self.settings["account_name"], ensure_ascii=False)
        self.evaluate(
            f"const expected={expected};const deadline={int(deadline.timestamp() * 1000)};"
            "const root=document.querySelector('.job-detail-container');"
            "const selected=document.querySelector('.job-card-wrap.active');"
            "const button=root?.querySelector('.op-btn-chat');"
            f"if(![...document.querySelectorAll('.label-text')].some(element=>element.innerText.trim()==={account}))throw new Error('Account changed');"
            "const visible=element=>element.getClientRects().length&&getComputedStyle(element).visibility!=='hidden';"
            "if([...document.querySelectorAll('[role=dialog],.dialog-wrap,.dialog-container,.greet-boss-container')].some(visible))throw new Error('Dialog');"
            "if(/访问异常|操作频繁|安全验证|验证码|拖动滑块|开聊次数.*用完|今日.*上限/.test(document.body.innerText))throw new Error('Restricted');"
            "if(Date.now()>=deadline||selected?.querySelector('.job-name')?.getAttribute('href')!==expected.job_key"
            "||root?.querySelector('.job-detail-info .job-name')?.innerText.trim()!==expected.title"
            "||selected?.querySelector('.boss-name')?.innerText.trim()!==expected.company"
            "||selected?.querySelector('.company-location')?.innerText.trim()!==expected.location"
            "||root?.querySelector('.job-salary')?.innerText.trim()!==expected.salary"
            "||root?.querySelector('.job-boss-info .name')?.innerText.trim()!==expected.recruiter"
            "||root?.innerText!==expected.text"
            "||button?.innerText.trim()!=='立即沟通'"
            "||!button.getAttribute('ka')?.includes(expected.job_key.split('/').at(-1).replace('.html','')))throw new Error('Changed job');"
            "button.click();return true;"
        )

    def verify(self, candidate: dict) -> dict:
        account = json.dumps(self.settings["account_name"], ensure_ascii=False)
        for attempt in range(16):
            result = self.evaluate(
                "const root=document.querySelector('.greet-boss-container');"
                "const visible=root?.getClientRects().length;return {"
                f"account:[...document.querySelectorAll('.label-text')].some(element=>element.innerText.trim()==={account}),"
                "job_key:document.querySelector('.job-card-wrap.active .job-name')?.getAttribute('href'),"
                "header:visible?root.querySelector('.greet-boss-header')?.innerText.trim():null,"
                "message:visible?root.querySelector('.greet-con')?.innerText.trim():null,"
                "restricted:/访问异常|操作频繁|安全验证|验证码|拖动滑块|开聊次数.*用完|今日.*上限/.test(document.body.innerText)};"
            )
            if result.get("restricted") or result.get("account") is not True:
                raise BrowserError("平台出现验证、限制或账号变化；停止且不重试发送")
            if (
                result.get("job_key") == candidate["job_key"]
                and result.get("header") == "已向BOSS发送消息"
                and candidate["title"] in (result.get("message") or "")
            ):
                return {"status": "greeting_verified", "evidence": result["header"], "job_key": candidate["job_key"]}
            self.sleep(0.5)
        raise BrowserError("没有取得同岗首次招呼回执；保留待核实，不重复点击")

    def dismiss_receipt(self) -> None:
        self.evaluate(
            "const root=document.querySelector('.greet-boss-container');"
            "if(root?.querySelector('.greet-boss-header')?.innerText.trim()!=='已向BOSS发送消息')throw new Error('Receipt changed');"
            "const button=root.querySelector('.greet-boss-footer .cancel-btn');"
            "if(button?.innerText.trim()!=='留在此页')throw new Error('Missing stay button');"
            "button.click();return true;"
        )

    def expand(self) -> bool:
        return self.evaluate(
            "const root=document.querySelector('.job-list-container')||document.scrollingElement;"
            "if(!root)throw new Error('Missing list');"
            "const before=root.scrollTop;root.scrollTop+=Math.max(300,root.clientHeight*0.8);"
            "return root.scrollTop>before;"
        )


class DemoBrowser:
    def __init__(self, candidates: list[dict]):
        self.candidates = candidates

    def preflight(self) -> None:
        pass

    def cards(self) -> list[dict]:
        return self.candidates

    def inspect(self, card: dict) -> dict:
        return dict(card)

    def expand(self) -> bool:
        return False

    def click(self, candidate: dict, deadline: datetime) -> None:
        raise BrowserError("演示适配器永远不能发送")
