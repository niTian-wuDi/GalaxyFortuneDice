# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Galaxy Fortune Dice（银河幸运骰子）— 多人在线 Yahtzee（快艇骰子）对战游戏后端。实时对战通过 WebSocket 频道系统驱动，对局状态存储在 Redis 中，持久化数据落 MySQL。

## Commands

```bash
# 启动服务（开发模式，热重载）
uvicorn main:app --reload --host 0.0.0.0 --port 8001

# 安装依赖
pip install -r requirements.txt

# 激活虚拟环境（Windows）
venv\Scripts\activate
```

Swagger 文档: `http://localhost:8001/docs`

项目无测试套件、无 linter 配置。

## Environment

需要 `.env` 文件（已在 `.gitignore` 中排除），配置项见 `config/db_config.py` 的 `Settings` 类：
- MySQL 连接信息（MYSQL_HOST/PORT/USER/PASSWORD/DB）
- Redis 连接信息（REDIS_HOST/PORT/DB）
- JWT 密钥（SECRET_KEY/ALGORITHM/ACCESS_TOKEN_EXPIRE_DAYS）

`pydantic-settings` 会自动从 `.env` 加载。

## Architecture

### 分层结构

```
routers/ (HTTP 路由 + 请求校验)
  → crud/ (数据库操作)
  → models/ (SQLAlchemy ORM 模型)
schemas/ (Pydantic 请求/响应模型，贯穿所有层)
utils/ (JWT 鉴权、统一响应封装)
websocket/ (WebSocket 连接管理 + 频道广播)
config/ (数据库连接、Redis 客户端、环境变量)
```

### 核心设计要点

1. **双存储架构**: Redis 存储对局实时状态（骰子、轮次、计分项），MySQL 存储持久化数据（用户、房间、对局记录、统计）。对局期间所有状态变更先写 Redis，结算时批量写 MySQL。

2. **WebSocket 频道系统**: `websocket/manager.py` 的 `ConnectionManager` 实现了基于频道的广播。频道命名约定：`room:{room_id}`、`match:{match_id}`。玩家从房间频道迁移到对局频道发生在 `routers/matches.py:start_match` 中。

3. **统一响应格式**: 所有 HTTP 接口通过 `utils/response.py:success()` 返回 `ApiResponse(code, msg, data)` 结构。

4. **JWT 鉴权**: HTTP 接口用 `Depends(get_current_user)` 做 Bearer Token 鉴权；WebSocket 通过 URL query 参数 `token` 鉴权，鉴权函数为 `get_current_user_websocket`。

5. **对局流程**: 房间准备 → 创建 Match 记录 → `/start` 初始化 Redis 状态 → 掷骰(最多3次) → 选分 → 下一玩家 → 13轮后结算（含上半部分奖励35分） → 写入战绩/统计 → 广播 `game_ended`。

### Redis Key 约定

- `room:{room_id}:players` — 房间玩家列表 (JSON string)
- `match:{match_id}:state` — 对局全局状态 (Hash)
- `match:{match_id}:player:{user_id}` — 玩家对局数据 (Hash: dice_values, locked_dice, used_scores, total_score, yahtzee_bonus_count)
- `ranking:total` — 总排行榜 (Sorted Set)
- `ranking:daily:{date}` — 每日排行榜 (Sorted Set)

### 计分逻辑

`routers/matches.py:calculate_score()` 实现完整的 Yahtzee 13 种计分规则。Yahtzee 奖励机制（首次50分，后续每次100分）在 `select_score` 接口中处理。上半部分奖励（ones~sixes 总分≥63 → +35分）在游戏结束结算时计算。

## Code Conventions

- 所有接口返回中文提示信息（msg 字段、HTTPException detail）
- ORM 模型表名以 `t_` 前缀命名
- Pydantic schema 与 ORM 模型分离定义在 `schemas/` 目录
- WebSocket 消息格式：`{"type": "事件名", "data": {...}}` 或直接在顶层放字段
- 使用 `pydantic-settings` 管理环境变量，settings 实例在 `config/db_config.py` 中创建并全局导出
