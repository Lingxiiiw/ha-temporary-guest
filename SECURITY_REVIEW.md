# Temporary Guest 0.3.1 安全核验

核验日期：2026-10-17。目标：HA 2026.10.1。用户要求修复日志泄露后，追加主动探测其他风险并修复。

## 证据范围

线上使用用户指定实例的受管访客账号做只读探测，只记录成功/拒绝、数量和敏感字段是否存在；没有保存日志正文、实体名称、坐标值、设备标识、密码或访问令牌。每次新建的探测会话均已通过 `/auth/token` 撤销。没有读取摄像头画面、没有执行服务或操纵设备，也没有部署或重启线上 HA。

本地使用实际 `homeassistant==2026.10.1` 包核对实现，并通过真实 HA 命令注册/分发、注册表 JSON 序列化和 aiohttp 请求路由做回归。测试数据库、设备与认证数据使用合成值，未复制线上记录。

## 已确认问题与处理

| 项目 | 修复前证据 | 0.3.1 处理 |
| --- | --- | --- |
| 活动日志 | WS/HTTP 最近 5 分钟各返回 7 条日志，正常 `get_states` 为 0 条；core handler 不检查用户实体权限 | 阻止受管访客构造日志处理器，历史、HTTP 和实时入口一起封堵 |
| 任意模板 | `render_template` 请求 `{{ states \| count }}` 返回 382，突破正常状态读取范围 | 在模板执行和建立监听前拒绝 |
| 实体注册表 | list 返回 884，display 返回 363；任意单条 get 也成功 | 只返回 read 白名单实体，单条越权与不存在统一 not_found；去除 unique_id、options、配置项等额外字段 |
| 设备/区域/标签 | 106 台设备、5 个区域、1 个标签；访客可建立注册表更新订阅 | 设备/区域/楼层仅保留可读实体关联的最小展示信息，标签与人员清单为空；禁止非状态原始事件订阅 |
| 关联搜索 | 以白名单外实体为入口可取得 3 类关系结果，core Searcher 无用户权限裁剪 | 拒绝关联搜索及额外注册表关系查询 |
| 配置与其他面板 | get_config 和 HTTP /api/config 含真实位置、内外网地址；面板返回 12 项，另一个 Dashboard 配置请求成功 | 配置用中性位置/空网络地址替代；面板和 Lovelace 配置请求限指定访客 Dashboard |
| 统计/能源 | `recorder/list_statistic_ids` 返回 161 项；源码统计查询无 read 检查 | 访客拒绝 recorder/energy API；普通 history 继续用 HA 原生权限过滤 |
| 图像/媒体 | camera、image、media_player 图像代理的源码只检查已登录或资源 token；播放器 browse/search 同样缺少实体授权 | 带访客身份的 HTTP 图像/流请求及 camera/media_player WS 请求先检查 read；全局 media_source 拒绝 |
| 服务入口与回包 | core 入口把调用交给各服务自行授权；HTTP 会收集同一调用上下文的全部变化状态 | 先要求显式 control 白名单实体，拒绝全局/区域/设备/标签/跨域/无目标；拒绝任意服务返回数据，REST 变化状态按 read 再过滤 |

全局组件/事件/服务 HTTP 清单不再向访客提供；WS 服务描述仅保留当前可控制实体所属域。服务描述不是执行权限，执行仍单独检查。

## 已核验原生保护

- `get_states`、`subscribe_entities`、`subscribe_events(state_changed)` 原本按实体 READ 过滤。
- HTTP/WS `history` 原本调用 `filter_entity_ids_by_permission`。线上指定白名单外实体的历史 WS 查询返回空对象；不传必需实体参数的调用返回 invalid_format，不能把它误记成已验证安全的全量查询。
- calendar 列表和事件查询原本检查 READ，写操作检查 CONTROL。
- admin-only 的模板 HTTP 接口、触发器订阅和管理写接口已有原生鉴权；本次额外防护不会给访客新增权限。

主要源码依据位于 HA 2026.10.1 的 `components/logbook/{rest_api,websocket_api}.py`、`components/config/*_registry.py`、`components/websocket_api/commands.py`、`components/search/__init__.py`、`components/lovelace/websocket.py`、`components/recorder/websocket_api.py`、`components/{camera,image,media_player}/__init__.py` 及 `components/api/__init__.py`。可在官方固定版本 [home-assistant/core 2026.10.1](https://github.com/home-assistant/core/tree/2026.10.1/homeassistant/components) 对照。

## 实现边界与兼容影响

- 仅受管 ID/专用组受限。Owner、管理员及其他用户仍走原 HA 处理器。
- 集成初始化先安装防护，再撤销旧访客会话并持久化；HA token 撤销回调会关闭 WS，清理已有实时订阅。已存在账号遇到安装防护失败时会停用。
- 防护保留到进程结束；卸载 manager 不会让专用组账号恢复旁路访问。重载时从配置重新取得用户身份。
- WebSocket 保留 HA 的 schema、消息 ID 顺序和错误格式；HTTP 包装既有路由与后加载 view，不改 HA 安装包文件。
- 访客日志、任意模板、统计/能源卡、全局媒体库不可用；需要这些接口的自定义卡片会报无权限。服务动作必须给出明确实体 ID，不支持 all、区域、设备目标或 return_response。
- 配置路径的首段用于识别 Dashboard。例如 `/dashboard-guest/view` 允许该 Dashboard 的配置，不是按视图裁剪。Owner 必须保证同一 Dashboard 全部视图都适合访客。
- 获准读取的实体自身属性、获准控制的脚本/场景内部动作，仍由 Owner 选择其授权范围。
- 本次没有对任意第三方扩展、公开静态资源、前端资源内容、无身份且持有有效资源 token 的请求建立全面隔离；也无法追回修复前泄露的数据。不是“所有 HA API 已安全”的证明。
- 防护使用 HA 内部方法和 aiohttp `_handler`，当前固定目标版本通过测试；升级 HA 后需重新验证，不能只看接口名称仍存在。

## 自动化验证

- 完整 Python 回归：`107 passed, 7 warnings`；警告来自上游弃用项与 aiohttp 请求键。
- 前端守卫测试：`2 passed`；JavaScript 语法检查通过。
- `ruff check custom_components tests tools` 和 Python 编译检查通过。
- 这些结果证明本地测试通过，不代表已部署或已完成真实 HA 浏览器验收。

## 上线后验收

1. 更新 `custom_components/temporary_guest` 并完整重启 HA；访客重新登录，旧访客 token 应失效。
2. 用新访客会话运行 `tools/security_probe.py`：日志/模板/统计/search 被拒绝；实体/设备清单只出现白名单相关记录；全局配置不含真实位置/网络地址；其他 Dashboard 被拒绝。
3. 对授权与未授权图像实体分别测试，未授权应在取图前返回 403。没有读权限的实体不得通过媒体 WS 获取流或媒体列表。
4. 确认访客面板正常显示授权实体并允许其显式实体目标控制；Owner 的日志、模板、媒体及其他面板不受影响。
5. 以上真实 HA 更新后的复测尚未完成；本次交付只包含本地代码、测试和安装包。
