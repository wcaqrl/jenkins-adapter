# Jenkins 免密登录注入说明

本项目按懒猫开发者手册的高级免密登录方案，在 `lzc-manifest.yml` 中组合 `request`、`response`、`browser` 三个阶段。注入只修改网关侧行为，不改 Jenkins 镜像。

## 工作流程

1. 首次访问 `/login` 时，`builtin://simple-inject-password` 使用默认的 `admin / peter111` 填充并提交 Jenkins 登录表单。
2. `/j_spring_security_check` 的 request inject 把表单字段 `j_username`、`j_password` 暂存在 `ctx.flow`。
3. response inject 只在 Jenkins 返回成功响应，且没有跳转到 `/loginError` 时，才把凭据写入 `ctx.persist`。
4. 用户从 `/user/<id>/security/` 修改密码时，request inject 读取 `user.password` 与 `user.password2`；二者非空且相等时暂存新密码，成功响应后再提交到 `ctx.persist`。
5. 后续访问 `/login` 时，browser inject 优先使用 `ctx.persist` 中的凭据自动登录。

`ctx.persist` 按懒猫 `SAFE_UID` 隔离。请求或响应脚本不会输出密码，也不会在请求阶段直接持久化失败的候选值。

## Jenkins 适配点

- 登录路径：`/login`
- 登录提交路径：`/j_spring_security_check`
- 用户名选择器：`#j_username`
- 密码选择器：`#j_password`
- 登录表单：`form[name="login"]`
- 提交按钮：`button[name="Submit"]`
- 网页改密提交路径：`/user/<id>/security/configSubmit` 或 `/me/security/configSubmit`
- 新密码字段：`user.password`
- 确认字段：`user.password2`

登录页匹配规则使用精确的 `/login`，不会命中 `/loginError`，避免无效凭据被反复自动提交。Jenkins 的网页改密表单不要求再次输入当前密码，因此没有在安全设置页填充密码字段。

## 验证步骤

1. 新装或清除该用户的 inject 持久值后打开应用，确认无需操作即可进入 Jenkins 首页。
2. 退出 Jenkins 后再次打开 `/login`，确认仍可自动登录。
3. 在“用户 → 安全”页面修改密码并保存，退出后确认新密码可自动登录。
4. 故意提交错误密码，确认跳转到 `/loginError` 后页面停留，不发生自动提交循环。

相关官方文档：

- <https://developer.lazycat.cloud/advanced-inject-passwordless-login.html>
- <https://developer.lazycat.cloud/advanced-injects.html>
- <https://developer.lazycat.cloud/spec/inject-ctx.html>
- <https://developer.lazycat.cloud/spec/manifest.html>
