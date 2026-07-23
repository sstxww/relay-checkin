# Relay Check-in

一个可扩展的 API 中转站每日签到工具。使用系统访问令牌直接发送 HTTP 请求，不依赖浏览器或 Cookie。

当前内置站点：

| 站点 | 官网 | 签到接口 |
| --- | --- | --- |
| SheApi | https://www.sheapi.top/ | `POST /api/user/checkin` |

## 特性

- 纯 Python 标准库，无第三方依赖
- 多站点配置，后续可继续添加中转站
- 凭证仅从环境变量读取，不写入配置或日志
- 已签到视为幂等成功，适合定时任务重试
- 提供 systemd 服务和定时器示例
- 默认仅允许 HTTPS 站点配置

## 快速开始

需要 Python 3.10 或更高版本。

1. 获取站点凭证。

   SheApi：打开“个人资料 -> 访问令牌”，获取系统访问令牌；用户 ID 在个人资料页显示。

2. 设置环境变量。

```bash
export SHEAPI_ACCESS_TOKEN='replace-with-system-access-token'
export SHEAPI_USER_ID='replace-with-user-id'
```

3. 验证配置，不发送请求。

```bash
python3 relay_checkin.py --dry-run
```

4. 执行签到。

```bash
python3 relay_checkin.py
```

成功、已签到时退出码均为 `0`；配置错误为 `2`；任一站点签到失败为 `1`。

## SheApi 鉴权说明

SheApi 使用以下请求格式：

```http
POST https://www.sheapi.top/api/user/checkin
Authorization: <system-access-token>
New-Api-User: <user-id>
Content-Type: application/json

{}
```

`Authorization` 后直接放系统访问令牌，不添加 `Bearer`。不要提交真实令牌、用户 ID、Cookie 或服务器信息。

## 添加中转站

在 `sites.json` 的 `sites` 数组中增加配置：

```json
{
  "id": "example",
  "name": "Example Relay",
  "homepage": "https://relay.example/",
  "base_url": "https://relay.example",
  "checkin_path": "/api/user/checkin",
  "access_token_env": "EXAMPLE_ACCESS_TOKEN",
  "user_id_env": "EXAMPLE_USER_ID",
  "already_checked_in_messages": [
    "already checked in"
  ]
}
```

然后在运行环境中设置 `EXAMPLE_ACCESS_TOKEN` 和 `EXAMPLE_USER_ID`。若新站点的请求或响应格式不同，应新增适配代码和测试，而不是把凭证写入仓库。

可使用 `--site sheapi` 只运行指定站点，也可重复传入 `--site`。

## systemd 部署

推荐部署在家庭网络、NAS 或独享固定 IP 的服务器上。下面假设仓库位于 `/opt/relay-checkin`：

```bash
sudo install -o root -g root -m 600 .env.example /etc/relay-checkin.env
sudo editor /etc/relay-checkin.env
sudo install -o root -g root -m 644 systemd/relay-checkin.service /etc/systemd/system/
sudo install -o root -g root -m 644 systemd/relay-checkin.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now relay-checkin.timer
sudo systemctl start relay-checkin.service
```

默认每天北京时间 `09:15` 执行，错过时间会在下次开机后补跑。查看日志：

```bash
journalctl -u relay-checkin.service -n 50 --no-pager
```

## 关于 GitHub Actions

不建议默认使用 GitHub Actions 自动签到。Actions 使用共享且可能变化的出口 IP，一些中转站会限制同一 IP 的签到账号数量。使用自己的固定出口 IP 更稳定，也更不容易触发风控。

## 安全

- `.env`、本地站点配置、日志和 Python 缓存已加入 `.gitignore`
- 建议将环境文件权限设置为 `600`
- 不要在 Issue、日志或截图中发布令牌、Cookie、用户 ID、服务器 IP
- 令牌失效后只需重新生成并更新环境变量，不需要每天获取

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试不会连接真实站点。

## License

[MIT](LICENSE)
