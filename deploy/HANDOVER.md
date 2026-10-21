# 常驻浏览器与人工接管部署

本版本增加独立 browser 服务、Worker 调度、应用级 NEEDS_HUMAN（数据库值 `needs_human`）及加密恢复。没有真实招聘回执就不会记为 submitted。测试中的 simulated 绝不计入真实投递。

## 独立服务器启动

需要 Linux、Docker Engine、Compose v2、Python 3；服务器须能正常访问招聘官网及其验证资源。无需 OpenAI API Key。当前没有用户提供的服务器，本仓库不代表已完成外部部署。

```bash
git clone https://github.com/liujessica532-coder/-career-autopilot-2027-.git
cd -- -career-autopilot-2027-
cp .env.example .env
# 在本机编辑 .env：设置新的 URL-safe POSTGRES_PASSWORD
chmod 600 .env
./deploy/start.sh
docker compose ps
docker compose logs --tail 100 browser worker
docker compose restart browser worker
```

`start.sh` 只在不存在时生成密钥，不覆盖已有文件。`private/` 为 0700；内部 secret 文件为 0644，使非 root 容器能读取绑定的 Compose secret。父目录限制宿主机访问；不要放宽父目录权限。宿主机管理员及 Docker 权限持有者仍能读取数据。state 使用 Fernet 加密（Cookie、localStorage、sessionStorage 和表单参考），密钥由 Compose 文件 secret 单独挂载。数据库、材料、截图需要磁盘加密和受控备份；它们不属于会话密文。备份数据库、artifacts、state 卷及密钥，密钥必须分开保管。不要执行 `down -v`。不要在线生成新密钥覆盖旧密钥。

浏览器端口只绑定服务器 loopback；不开放 CDP、VNC 或远程调试端口。Chromium 运行于 Xvfb，允许正常交互验证，不包含验证码破解逻辑。一个 state 卷只允许一个 browser 服务。Worker 不持有会话加密密钥。

## 人工接管

在自己的电脑通过 SSH 转发：

```bash
ssh -N -L 8765:127.0.0.1:8765 USER@HOST
```

在本地浏览器打开 loopback 8765 端口。通过可信服务器终端读取 `private/operator.token`，在登录框输入；不要发送到聊天、URL、Git 或日志。登录后选择暂停申请，获得只绑定该申请的 10 分钟 HttpOnly Cookie。令牌不存入 localStorage；新接管使旧接管失效。界面显示实际远程浏览器截图，鼠标、拖动、滚轮和键盘输入发送到原浏览器。输入框内容发送后清空，审计仅记录操作类型。正常完成登录、短信或验证码后，检查申请内容，点击“已完成人工验证，继续”。如果网站仍提示验证，当前申请继续暂停，其他申请照常调度。

如果采用 HTTPS 反向代理，配置 `HANDOVER_ORIGINS` 为精确 HTTPS origin，代理只允许可信操作者访问；禁止开放裸 HTTP。默认 SSH 转发不需要改 origin。不要让代理记录 Authorization、Cookie 或请求正文。

## 任务与恢复规则

`python -m scripts.manage enqueue-application` 的现有接口仍可使用。Compose Worker 将任务交给 browser 服务，等待人工时不占 Worker。申请材料必须属于该申请且 SHA256 匹配；真实上传必须有经审阅的接受提示 selector。

站点 spec 保留现有 steps，同时需要 `entry_url`、精确 `allowed_origins`。真实申请需 verified 候选人、24 小时内复核的 eligible 岗位、reviewed spec。自动提交还需 authorized、submit_selector、receipt_selector、receipt_text_pattern，建议增加 receipt_url_pattern、receipt_number_selector。涉及未知必填项、同意声明时暂停人工检查。`start_paused:true` 可先进入原站点让人工操作，不自动推进。不要把历史失败页面直接作为新任务启动。

```bash
# 命令在服务器仓库中执行；使用容器内的真实数据库、token、材料路径
# 私有 spec 放在 artifacts 卷中，不提交 Git
docker compose exec -T browser python -m scripts.browser_admin list --token-file /run/secrets/operator_token
docker compose exec -T browser python -m scripts.browser_admin start --token-file /run/secrets/operator_token --application-id ID --spec artifacts/private/site.json
```

重启只恢复加密 Cookie/storage，并进行原网址的 GET；**不会自动重放 POST、上传、步骤点击或最终提交**。恢复后重新签发接管，人工核对网页和游标再继续。服务器可能丢弃表单状态，登录或验证码可能过期，不能保证无人工恢复任意网站。中断的步骤点击需要先核对雇主端，再执行 `browser_admin cursor --session-id UUID --cursor N`；该命令明确记录人工核对。最终点击前先写 submission intent；点击结果不明确则 submission_uncertain，禁止再次自动点击。

人工在官网完成最后提交后，可用 `browser_admin inspect` 读取当前页面结构，再创建私有 review JSON：`confirmed_receipt_review:true`、精确 receipt_selector、receipt_text_pattern、receipt_url_pattern，及可选 receipt_number_selector。执行 `browser_admin receipt-review --session-id UUID --review artifacts/private/receipt.json`。它只检查当前实际页面并保存截图/HTML/SHA256，不点击提交。匹配失败不改为成功。成功才保存提交时间、编号、材料关联并解决 HumanAction。不要使用宽泛的任意文本正则作为回执条件。

## 搬迁已有暂停记录

当前本地真实 SQLite 与旧浏览器 profile 均保留；迁移包 `private/records.enc` 已使用 `private/browser-state.key` 加密，仅包含数据库六类记录和记录关联的真实材料，不包含 Cookie、旧 profile、任务队列。它们都被 Git 忽略。DHL 377487、387006 保持 needs_human；385059 保持 not_started；没有新增真实提交。

通过安全渠道把迁移包与其密钥分别传到服务器 private 目录，在首次初始化前放好原密钥。新服务器仍应生成新的 operator token。导入期间停止 browser/worker；目标必须为空候选人/申请数据库，不覆盖已有数据，不重放任务。先构建镜像及启动数据库，再导入：

```bash
python3 deploy/init_secrets.py
docker compose build
docker compose up -d db
docker compose stop browser worker
# DB healthy 后执行；private 目录只读挂载，不写入镜像
docker compose run --rm --no-deps -v "$PWD/private:/import:ro" browser python -m scripts.records_bundle import --input /import/records.enc --key-file /run/secrets/browser_state_key
docker compose up -d --wait
```

导入输出旧/新 ID 对照；重复同一个包不重复导入。旧暂停记录不会自动打开 DHL。人工决定恢复时先复核岗位开放、资格、官网状态，再建立 reviewed browser session。旧 profile 不迁移，可能需要重新登录或重新上传真实材料。

## 网络诊断和验收限制

访问失败分类：network_blocked、page_timeout、login_expired、captcha、form_error。HTTP 403 本身无法证明由哪一方阻断；当前 Codex 运行策略未列入 recaptcha.net，策略生效状态 unknown，域名配置草稿不等于已发布。必须在环境设置保存并发布，独立确认新的版本及域名后，才使用 `scripts/recaptcha_check.py` 做一次正常 TLS 请求；403 后停止。不能以更换出口、禁用 TLS 或伪造验证替代正常访问。

Codex 专用构建使用 `python3 deploy/cloud-build.py` 和 `python3 deploy/cloud-up.py`：只复用平台现有代理，CA 通过只读 build secret/runtime mount 提供，不写镜像；不采用 host network 或关闭签名检查。普通服务器用 start.sh。

测试：`python -m pytest -q`。隔离的真实 Chromium + 本地合成网站验证接管、重启恢复、独立暂停、回执和防重提；这些不证明 DHL 实际提交。真实验收仍需独立服务器正常验证后取得招聘网站回执。没有服务器时只能交付部署产物和机制测试证据。
