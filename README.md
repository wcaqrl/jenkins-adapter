# Jenkins · 懒猫微服自动更新适配器

包名 **`peter.lazycat.app.jenkins`**。本仓库独立维护原 `wcaqrl/jenkins`
的定制镜像与懒猫配置，复用
[`lazycat-action@v1`](https://github.com/wcaqrl/lazycat-action)，无需修改 Jenkins 上游。
构建与应用验证在 GitHub Runner 上完成，不占用本机或 LightOS 的 Docker 资源。

## 更新流程

```text
官方 docker.io/jenkins/jenkins 的 LTS 版本标签
  → 选择 x.y.z-jdk21 的 linux/amd64 摘要
  → 在同一固定基础镜像上加入工具链、插件、JCasC、密码初始化
  → 测试新装 + 商店旧版数据升级 + 更换容器 + 密码重置
  → 将同一个通过测试的定制镜像推送 ttl.sh（24 小时临时中转）
  → lazycat-action 转存 registry.lazycat.cloud，更新 Manifest
  → 再核对测试摘要、配方与最终交付镜像，生成 .lpk
  ├─ 提交官方应用商店审核 → 保存审核记录及状态到本仓库
  └─ 创建本仓库 GitHub Release → 上传带版本号的 .lpk
```

中转镜像不需要 Docker 账号。最终用户只使用懒猫官方仓库；不会从 ttl.sh
运行应用。不需要企业镜像仓库或旧 `wcaqrl/jenkins` 的 GHCR 包。
中转不可用、摘要不一致或任一测试失败时，本次流程失败，不提交未经验证的镜像。

版本判断规则的注释在 [lazycat-action.yml](lazycat-action.yml)。只比较 LTS
版本号，不以 tag 创建时间、weekly、`latest` 或 `lts-jdk21` 滚动别名判断。
`-jdk21` 是镜像变体，提取 `x.y.z` 后再做数值比较，不能误当作预发布版本。
本次迁移探测到 LTS `2.580.2`。提交时以当次实际发现的版本及摘要为准。

## 保留的定制内容

- Jenkins LTS / Java 21，Python 3.12、Git、Go、Node.js、npm 与构建工具。
- Git、Pipeline、JCasC、凭据绑定、中文本地化、深色主题等原有推荐插件。
- 初始管理员 `admin / peter111`，支持网页改密；重启和升级不覆盖已修改密码。
- 按懒猫官方 injects 约定自动登录，网页成功改密后更新对应微服用户的登录凭据。
- `JENKINS_HOME=/lzcapp/var/jenkins`，工作空间、任务、用户、插件和加密密钥持久化；
  Go/npm/pip 缓存分别位于 `/lzcapp/cache` 下。镜像使用 UID/GID `1000:1000`。
- 动态 `https://{{ .S.AppDomain }}/`、管理员应用、不支持 Android/iOS 启动器。
  当前自定义工具链只构建 `linux/amd64`，没有声明支持 arm64。

工具链版本及 Python 镜像摘要固定在 [image/versions.lock.json](image/versions.lock.json)，
迁移保留 Go `1.27.1`、Node `26.8.2`、npm `12.0.2`。Git 来自基础发行版的软件源；
Python 的补丁版本取决于锁定的 Python 镜像。运行验证报告记录实际全部版本。
插件沿用原项目的推荐列表和 `jenkins-plugin-cli --latest true` 依赖解析，
每次新镜像构建时解析兼容版本，再验证所有推荐插件处于 active 状态；
这不是一份固定插件版本的可复现构建，实际插件版本记录在验证报告中。

## 首次配置与执行

1. 将本仓库代码提交、推送到 GitHub 的默认 `main` 分支。
2. Settings → Secrets and variables → Actions → Repository secrets 中新增
   **`LZC_API_TOKEN`**，填懒猫开发者 PAT。该账号须有 `peter.lazycat.app.jenkins`
   的开发者或协作者权限；不需要把 PAT 配在 `lazycat-action` 仓库。
3. Actions → **Update Jenkins for LazyCat** → **Run workflow**。
   默认勾选 `dry-run` 只发现版本、提取日志、计算方案。
4. 保持 `dry-run` 勾选，再勾选 `verify-image`，在 Runner 构建并执行真实升级测试，
   不上传镜像、不生成 LPK、不送审。结果在验证 Artifact 中。
5. 准备送审时取消 `dry-run`。`verify-image` 在真实执行时不用勾选，正式流程本身
   强制构建和测试；成功后核对审核记录、Release 的版本化 LPK 和状态提交。
6. 定时任务与 Immich、Memos、Plex 等适配器一致，直接检查并发布新版本，
   **不需要配置额外的 Actions Variable**。切换后应停用旧 `wcaqrl/jenkins`
   仓库的构建/发布工作流，避免两个仓库同时处理同一应用。

每 6 小时检查一次（北京时间约 02:41、08:41、14:41、20:41，GitHub 可能延迟）。
推送代码只触发只读检查；手动执行默认仍为 `dry-run`。
若仓库要求人工批准首次 Actions 运行或禁止工作流写入内容，需要按运行提示调整。

## 版本、日志与状态

`package.yml` 初始保留线上基线 `1.0.2`，不是伪造新提交。首次正式更新会按当前
通用执行器规则使用上游 `2.580.2` 作为应用版本（若已占用则分配更高补丁版本），
不会降回 `1.0.x`。旧包号与 Jenkins 核心版本不同，基线信息单独保存在
[image/ci/baseline.json](image/ci/baseline.json)。

更新日志只提取 [Jenkins 官方 LTS changelog](https://www.jenkins.io/changelog-stable/)
中本次版本的完整独立一节，缺失或重复则中止。原始日志按英文提交，由官方应用商店
服务端补齐另一语种；不会把同一段英文伪装成中文，也不会提交整页历史。

`.lazycat-action.lock.yml` 由执行器在真实打包/送审后生成并提交，**必须跟踪**。
不预先创建假的已提交状态。它记录源摘要、定制镜像、LPK 哈希和审核历史。
已处理或被拒的同一源版本不会每次定时重复提交；修复被拒版本后手动勾选
`retry-rejected`，并取消仅检查。更高上游版本可以替换旧的待审核版本。

Release 与送审在打包后分别执行；Release 失败不会阻止送审。只上传 `.lpk`，
不上传 `.sha256`。GitHub 自带的 Source code zip/tar.gz 无法由发布脚本关闭。

## 验证与维护

```bash
python3 -m pip install -r image/requirements-build.txt
sh -n image/entrypoint.sh
python3 -B -m unittest discover -s tests -v
# 有通用执行器的本地 checkout 时，连同实际 YAML 编辑器集成回归一起运行：
LAZYCAT_EXECUTOR_DIR=/path/to/lazycat-action python3 -B -m unittest discover -s tests -v
python3 -B image/ci/upstream.py candidate
LAZYCAT_SOURCE_VERSION=2.580.2 LAZYCAT_SOURCE_TAG=2.580.2-jdk21 \
  python3 -B image/ci/upstream.py changelog
```

`image/ci/prepare.py` 消费执行器选择的**确切版本和平台摘要**，不在准备阶段
重新寻找 latest。下载、候选 Docker 上下文、测试数据都在临时目录。
`image/ci/validate.py` 依次启动容器，最多一台 Jenkins 占用 2 CPU / 3 GiB；
失败退出时清理测试容器和数据。它验证：

- 空目录新装、初始账号、推荐插件、深色主题；
- 商店 `1.0.2` 中真实 Jenkins `2.568.3` → 新核心，共用持久目录；
- 改过的管理员密码、加密凭据解密、真实 Pipeline、历史构建与归档产物；
- Python 模块/venv/pip、Git commit、Go 编译执行、Node/npm 测试；
- 新容器再次挂载同一目录，以及显式密码重置后数据仍在。

旧版基线从懒猫官方 `registry-static.lazycat.cloud` 静态镜像 CDN 读取，
路径约定与应用商店服务端一致。短时只读端点仅监听 Runner 的 `127.0.0.1`，
向 Docker 返回锁定的原始 Manifest，并将其中的分层请求重定向到官方 HTTPS CDN。
Manifest 摘要、Docker 校验的分层摘要及加载后的配置 ID 都必须与商店旧镜像一致；
不需要懒猫 Docker 仓库密码，不替换成另一个重建镜像，也不改 Docker daemon 配置。

`lzc-build.yml` 的打包脚本会拒绝缺少验证证明、摘要不符或测试后被修改的配方。
Manifest 和 Package 按解析后的配置内容校验，允许执行器改写空行、缩进、引号
及键顺序；仅放行指定服务的镜像引用和应用版本变更。镜像摘要另行核对，
injects 脚本内容、字段类型、权限与其余构建文件的变化仍会阻止打包。
因此不能跳过镜像验证，直接在本机 `lzc-cli project build` 冒充已验证的新包。
无需修改通用 `lazycat-action`，旧项目也暂不删除。

## 忘记密码

在**应用服务容器内**，用 Jenkins UID 写入并重启应用：

```sh
printf '%s\n' '你的新密码' > /lzcapp/var/jenkins/secrets/reset-admin-password
chmod 600 /lzcapp/var/jenkins/secrets/reset-admin-password
```

该文件在下次启动时消费。`/lzcapp/var` 是应用容器中的路径，SSH 到微服宿主机
后不能直接假定同名路径存在。界面修改后的密码以 Jenkins 用户配置中的哈希为准，
`secrets/admin-password` 只保存初始化或最后一次文件重置时的密码，不会跟随网页改密更新。
文件重置后，若网关仍记得旧凭据，需手动登录一次新密码以更新该微服用户的 persist。

## 来源

定制代码迁移自 `wcaqrl/jenkins@7733fae2145991c8b2e7f543e8dac641e427c780`；
许可及图标来源见 [NOTICE.md](NOTICE.md)。
免密登录细节见 [docs/passwordless-login.md](docs/passwordless-login.md)。
参考：[Jenkins 官方 Docker 文档](https://www.jenkins.io/doc/book/installing/docker/)、
[懒猫 Manifest](https://developer.lazycat.cloud/spec/manifest.html)。
