# LinkedIn Easy Apply（真实投递尚待 Cloud Studio 验证）

适配器复用 `workers.main` 和 `Task`，无需付费 API 或新服务。自动发现只保存待审职位；不会把年份关键词当作资格核验。明确 2027 届优先，其次可能接受 2027 年毕业生的职位；城市优先顺序为上海、北京、深圳、广州、香港、澳门。香港/澳门工作资格或雇主担保需要真实核验。

## Cloud Studio 更新和运行

在现有仓库中拉取更新，不覆盖工作区私有文件或重建工作区：

```bash
git pull --ff-only origin main
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export CHROMIUM_EXECUTABLE=/usr/bin/chromium
.venv/bin/python -m scripts.manage init
CHROMIUM_EXECUTABLE=/usr/bin/chromium .venv/bin/python -m pytest -q -ra
```

`artifacts/real-validation/resume.pdf`、`profile.json` 和 `photo.jpg` 只保留在 Cloud Studio。适配器直接上传该 `resume.pdf`，从不重建或修改它。照片不会自动上传到未审阅的控件。继续使用现有已核验 Candidate；私有 JSON（允许不包含 `source_text`）必须与该数据库记录完全一致，原始 PDF 必须与已核验来源的 SHA256 一致。如果数据库中没有 Candidate，先用现有 `scripts.manage import` 和 `verify-profile` 流程审阅，不要使用测试候选人。

表单答案来自已核验 JSON 中的 `linkedin_answers`，键为页面显示的**完整标签**，值为真实答案。新增答案后，按现有流程再次确认数据库 Candidate；不推断学历、工作年限、签证、薪资等答案。未知问题（包括未核验的预填内容）、单选、多选、法律声明均暂停当前申请。可选 Cover Letter 留空；必填文字仅拼接已核验原文事实。必填 PDF 求职信生成到忽略目录，使用英文原文事实；中文 PDF 需另行审阅字体模板，不生成乱码。

浏览器使用 `state/browser/`。该目录含登录凭据，只保留在受限本机目录，不提交、不复制到日志。停止使用同一 profile 的其他浏览器/旧执行器后，在支持图形浏览器的 Cloud Studio 桌面中手动登录：

```bash
.venv/bin/python -m scripts.linkedin login
.venv/bin/python -m scripts.linkedin probe --url https://www.linkedin.com/jobs/view/4481983174/
```

`login` 不接受密码参数；用户在本地浏览器处理登录和验证。无图形显示的终端不能运行 headful 登录。`probe` 只读页面，不点击申请、不上传文件；可读取带登录提示的公开职位描述，但不把公开信号当作已登录核验。截图、描述及 JSON 位于 `artifacts/linkedin/probes/`；没有关闭信号不能证明仍开放，没有按钮也不能证明职位不支持 Easy Apply。

## 先审阅一个岗位，再进行首次真实验证

先核对职位仍开放、确实支持 Easy Apply、毕业时间、岗位要求、工作地点和工作资格。第三方 Apply 跳转不会被处理。2026-10-25 公开页面检查显示首个候选职位 `4481983174` 是 DigiFinex Global 的“香港综合行政-应届生”：职位描述未明确2027届，公开页无可确认的 Easy Apply 按钮、未见关闭信号且有登录提示，因此其开放状态、Easy Apply 和资格均未完成核验。若登录检查后关闭或不适合，使用下方发现命令寻找替代职位。

保存私有岗位记录到 `artifacts/real-validation/linkedin-job.json`。结构如下（占位符必须由真实页面证据替换，不能直接运行）：

```json
{
  "url": "https://www.linkedin.com/jobs/view/4481983174/",
  "title": "从当前职位页面读取",
  "jd": "从当前职位页面读取完整要求",
  "resume_confirmation_selector": "经审阅的服务端上传接受提示 selector",
  "evidence": {
    "open": {"confirmed": true, "quote": "实际证据", "url": "https://www.linkedin.com/jobs/view/4481983174/", "checked_at": "实际带时区 ISO 时间"},
    "accepts_2027": {"confirmed": true, "quote": "实际证据", "url": "https://www.linkedin.com/jobs/view/4481983174/", "checked_at": "实际带时区 ISO 时间"},
    "location": {"confirmed": true, "quote": "实际证据", "url": "https://www.linkedin.com/jobs/view/4481983174/", "checked_at": "实际带时区 ISO 时间"},
    "work_rights": {"confirmed": true, "quote": "真实工作资格/担保证据", "url": "https://www.linkedin.com/jobs/view/4481983174/", "checked_at": "实际带时区 ISO 时间"}
  }
}
```

上传成功 selector 必须代表服务端已接受文件，不能仅匹配 file input 或文件名；UI 改版时重新审阅。必填 PDF 求职信还需要 `cover_confirmation_selector`。证据必须在最近24小时内。不要为了通过检查编造证据。

```bash
.venv/bin/python -m scripts.linkedin check-materials --candidate-id CANDIDATE_ID
.venv/bin/python -m scripts.linkedin review-job --record artifacts/real-validation/linkedin-job.json --confirmed
# JOB_ID 为上一条命令输出；此命令明确授权这一份真实申请的最终提交。
.venv/bin/python -m scripts.linkedin enqueue --candidate-id CANDIDATE_ID --job-id JOB_ID --validation --submit-authorized
# 使用原有 Worker；不要并行启动多个使用同一浏览器 profile 的 Worker。
.venv/bin/python -m workers.main
```

Worker 内置240秒任务截止时间；普通发现失败按原有队列退避恢复。申请遇到未知控件、登录、验证码或中断则设为 `human_required`，释放队列，其他任务继续执行。人工检查并更新已核验答案、登录状态、岗位证据后，可恢复**提交前**暂停的草稿：

```bash
.venv/bin/python -m scripts.linkedin resume --application-id APPLICATION_ID --confirmed-draft-reviewed
```

恢复从当前官网草稿重新检查，不自动回放中断步骤。法律声明/未知控件尚无通用人工接管适配；它们可能继续暂停，需要在 LinkedIn 人工处理或单独适配，不能通过移除检查来继续。

最终点击前先提交数据库 `submitting` 意图；仅在 LinkedIn 当前页出现明确提交成功回执后保存 `submitted`、职位 URL、UTC 提交时间、回执文字及截图/SHA256（`artifacts/linkedin/APPLICATION_ID/`）。不明确的结果保持 `submission_uncertain`，禁止自动重试；需人工在 LinkedIn 核对回执，当前 CLI 不提供强行标记成功的功能。已成功申请按规范化职位 ID 和原始简历哈希去重，同时保留现有候选人/职位唯一约束。

## 真实验证通过后才开放批量入队

首次真实验证必须生成 `linkedin_real_e2e_verified` 事件，且对应候选人的真实成功记录、回执和截图哈希仍有效；模拟测试不会解锁生产库。未通过前 `enqueue-ranked` 会拒绝执行。

```bash
.venv/bin/python -m scripts.linkedin discover --search-url 'https://www.linkedin.com/jobs/search/?keywords=2027&location=Shanghai&f_AL=true'
# 每小时自动发现（最多6个搜索 URL、每轮20个职位）；仍使用原 Worker。
export LINKEDIN_SEARCH_URLS_JSON='["https://www.linkedin.com/jobs/search/?keywords=2027&location=Shanghai&f_AL=true"]'
.venv/bin/python -m workers.main
# 发现后按上文逐个 review-job；只有已审阅合格职位才入队，每次最多10份。
.venv/bin/python -m scripts.linkedin enqueue-ranked --candidate-id CANDIDATE_ID --submit-authorized
```

发现采用当前 UI 的有界扫描，不保证穷尽结果，不绕过 LinkedIn 验证或限流。自定义控件、非中英文按钮、必填未支持附件、LinkedIn DOM 变化都可能暂停。现有 Compose 的专用 browser 服务尚未接入此适配器；在 Cloud Studio 本机 Worker 运行，不能假定容器拥有本机资料和 profile。

测试数据、上传和成功回执均为本地合成场景，不是 LinkedIn 真实申请。仅代码、文档和合成测试可提交 Git；`artifacts/`、`state/`、`private/`、Cookie 和凭据始终保持忽略。当前 Codex 网络设置草稿另加入 `www.linkedin.com`、`linkedin.com`、`*.licdn.com`，保存草稿不代表已生效，也不会改变 Cloud Studio 网络设置。
