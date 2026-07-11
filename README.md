# V2RayN 公益节点测速订阅

此仓库每 6 小时获取一次已经过真实代理流量验证的公益节点，再使用 Mihomo 逐个完成二次测试。只有同时满足以下条件的节点才会进入订阅：

- HTTP 代理请求延迟不超过 400 ms
- 通过该节点下载 4 MiB 测试数据，平均速度不低于 2 MiB/s
- 协议属于 VMess、VLESS、Trojan、Shadowsocks、Hysteria2 或 TUIC

订阅地址：

```text
https://limeihui110.github.io/v2ray-speed-sub/sub.txt
```

测速结果：

```text
https://limeihui110.github.io/v2ray-speed-sub/status.json
```

## 说明

测速在 GitHub Actions 云服务器上执行，结果代表云服务器到节点的网络质量，和本机网络可能存在差异。公益节点随时可能失效；如果某次更新没有任何节点达标，流程会失败并保留上一次成功生成的订阅。

上游节点来自 [Au1rxx/free-vpn-subscriptions](https://github.com/Au1rxx/free-vpn-subscriptions)，该项目会先执行 TCP、TLS、配置校验和真实 HTTP 代理验证。本仓库只处理公开节点，不保存私人订阅。
