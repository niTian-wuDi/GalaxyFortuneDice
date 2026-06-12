from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import random
from datetime import datetime
from config.db_config import get_db, get_redis
from models.user import User
from utils.security import get_current_user
from schemas.match import (
    MatchStart, MatchState, MatchStartResponse, MatchUserInfo,
    RollDice, RollDiceResponse, SelectScore, SelectScoreResponse, GameRecordResponse
)
from crud.match import create_game_record, get_match_by_id, get_game_records_by_match, create_match_score_sheet, update_match, get_upper_section_score
from crud.user import update_user_total_score, update_user_history_stats, update_user_daily_stats, get_user_by_id
from crud.room import get_room_by_id
from crud.redis_manager import RedisManager
from websocket.manager import manager
from utils.response import success

router = APIRouter(prefix="/api/match", tags=["对局"])

# 计分项类型列表
SCORE_TYPES = [
    "ones", "twos", "threes", "fours", "fives", "sixes",
    "three_of_a_kind", "four_of_a_kind", "full_house",
    "small_straight", "large_straight", "yahtzee", "chance"
]

@router.post("/start")
async def start_match(match_data: MatchStart, db: Session = Depends(get_db), redis = Depends(get_redis), current_user: User = Depends(get_current_user)):
    """初始化对局状态（Match记录已由rooms.player_ready创建）"""
    match = get_match_by_id(db, match_data.match_id)
    if not match:
        raise HTTPException(status_code=404, detail="对局不存在")

    redis_manager = RedisManager(redis)
    room_players = redis_manager.get_room_players(match.room_id)
    if not room_players:
        raise HTTPException(status_code=400, detail="房间中没有玩家")

    # 随机选择起始玩家
    start_index = random.randint(0, len(room_players) - 1)
    first_player = room_players[start_index]

    # 初始化对局状态到 Redis
    match_state = {
        "match_id": match.id,
        "room_id": match.room_id,
        "current_round": 1,
        "current_turn_user_id": first_player["user_id"],
        "current_seat_no": first_player["seat_no"],
        "phase": "THROWING",
        "remain_throw_count": 3,
        "dice_values": [],
        "locked_dice": [],
        "selectable_scores": []
    }
    redis_manager.set_match_state(match.id, match_state)

    # 初始化每个玩家的实时数据，并将玩家从房间频道移动到对局频道
    for player in room_players:
        redis_manager.init_player_data(match.id, player["user_id"])
        # 将玩家从房间频道移动到对局频道
        manager.leave_channel(f"room:{match.room_id}", player["user_id"])
        manager.join_channel(f"match:{match.id}", player["user_id"])

    # 构建玩家信息列表
    match_info = [
        MatchUserInfo(
            user_id=p["user_id"],
            nickname=p["nickname"],
            team_id=p["team_id"],
            seat_no=p["seat_no"],
            ready_status=p["ready_status"],
            is_online=p["is_online"]
        ) for p in room_players
    ]

    # 广播对局详情给所有玩家（使用 match 频道）
    await manager.broadcast(
        f"match:{match.id}",
        {
            "type": "match_ready",
            "data": {
                "match_id": match.id,
                "room_id": match.room_id,
                "players": [m.model_dump() for m in match_info],
                "first_player_id": first_player["user_id"],
                "game_mode": match.game_mode
            }
        }
    )

    return success(MatchStartResponse(match_id=match.id, match_info=match_info), msg="对局初始化成功")

@router.get("/state")
async def get_match_state(match_id: int, db: Session = Depends(get_db), redis = Depends(get_redis), current_user: User = Depends(get_current_user)):
    redis_manager = RedisManager(redis)
    state = redis_manager.get_match_state(match_id)
    if not state:
        raise HTTPException(status_code=404, detail="对局不存在")
    
    return success(MatchState(
        match_id=match_id,
        room_id=state.get("room_id", 0),
        current_round=state.get("current_round", 1),
        current_turn_user_id=state.get("current_turn_user_id", 0),
        current_seat_no=state.get("current_seat_no", 0),
        phase=state.get("phase", "THROWING"),
        remain_throw_count=state.get("remain_throw_count", 3),
        dice_values=state.get("dice_values", []),
        locked_dice=state.get("locked_dice", []),
        selectable_scores=state.get("selectable_scores", [])
    ), msg="获取对局状态成功")

@router.post("/roll_dice")
async def roll_dice(roll_data: RollDice, db: Session = Depends(get_db), redis = Depends(get_redis), current_user: User = Depends(get_current_user)):
    redis_manager = RedisManager(redis)
    state = redis_manager.get_match_state(roll_data.match_id)
    if not state:
        raise HTTPException(status_code=404, detail="对局不存在")
    
    # 检查对局是否已结束
    match_status = state.get("status", "playing")
    if match_status == "finished":
        raise HTTPException(status_code=400, detail="对局已结束")
    
    # 从对局状态获取骰子信息
    remain_throws = state.get("remain_throw_count", 3)
    current_dice = state.get("dice_values", [])
    
    if remain_throws <= 0:
        raise HTTPException(status_code=400, detail="投掷次数已用完")
    
    # 生成骰子值
    if not current_dice:
        # 第一次投掷
        dice_values = [random.randint(1, 6) for _ in range(5)]
    else:
        # 后续投掷，根据锁定状态更新
        dice_values = current_dice.copy()
        if roll_data.lock_mask:
            for i in range(5):
                if not roll_data.lock_mask[i]:
                    dice_values[i] = random.randint(1, 6)
        else:
            dice_values = [random.randint(1, 6) for _ in range(5)]
    
    # 更新对局状态中的骰子信息
    remain_throws -= 1
    state["remain_throw_count"] = remain_throws
    state["dice_values"] = dice_values
    state["locked_dice"] = roll_data.lock_mask or []
    
    # 获取当前玩家已选择的计分项
    player_data = redis_manager.get_player_data(roll_data.match_id, roll_data.user_id) or {}
    selected_types = set(player_data.get("used_scores", []))
    
    # 计算可选分数，过滤已选的类型
    selectable_scores = []
    for score_type in SCORE_TYPES:
        if score_type not in selected_types:
            score = calculate_score(dice_values, score_type)
            selectable_scores.append({"type": score_type, "score": score})
    
    state["selectable_scores"] = selectable_scores
    state["phase"] = "SELECTING"
    redis_manager.set_match_state(roll_data.match_id, state)
    
    # 广播骰子结果给其他玩家
    await manager.broadcast(
        f"match:{roll_data.match_id}",
        {
            "type": "dice_rolled",
            "user_id": roll_data.user_id,
            "dice_values": dice_values,
            "locked_dice": roll_data.lock_mask or [],
            "remain_throws": remain_throws,
            "selectable_scores": selectable_scores
        },
        exclude_user_id=roll_data.user_id
    )
    
    return success(RollDiceResponse(dice_values=dice_values, remain_throw_count=remain_throws), msg="投掷骰子成功")

@router.post("/select_score")
async def select_score(score_data: SelectScore, db: Session = Depends(get_db), redis = Depends(get_redis), current_user: User = Depends(get_current_user)):
    redis_manager = RedisManager(redis)
    
    # 获取对局状态
    state = redis_manager.get_match_state(score_data.match_id)
    if not state:
        raise HTTPException(status_code=404, detail="对局不存在")
    
    # 检查对局状态是否正常
    match_status = state.get("status", "playing")
    if match_status == "finished":
        raise HTTPException(status_code=400, detail="对局已结束")
    
    # 从对局状态获取骰子信息
    dice_values = state.get("dice_values", [])
    if not dice_values:
        raise HTTPException(status_code=400, detail="请先投掷骰子")
    
    # 获取当前可选的计分项列表
    selectable_scores = state.get("selectable_scores", [])
    selectable_types = [s["type"] for s in selectable_scores]
    
    # 验证选择的分数类型是否在可选列表中
    if score_data.score_type not in selectable_types:
        raise HTTPException(status_code=400, detail=f"无效的计分项选择: {score_data.score_type}")
    
    # 计算分数
    round_score = calculate_score(dice_values, score_data.score_type)
    
    # 检测是否是 Yahtzee（5个相同）
    is_yahtzee = len(set(dice_values)) == 1 and len(dice_values) == 5
    
    # Yahtzee 奖励机制
    yahtzee_bonus = 0
    if is_yahtzee:
        player_data_temp = redis_manager.get_player_data(score_data.match_id, score_data.user_id)
        yahtzee_bonus_count = player_data_temp.get("yahtzee_bonus_count", 0) if player_data_temp else 0
        
        # 第一次 Yahtzee：在 "yahtzee" 格填分时，标记计数器
        # 后续 Yahtzee：任何计分项都给 100 分奖励
        if score_data.score_type == "yahtzee" and yahtzee_bonus_count == 0:
            # 第一次在 yahtzee 格填分，标记计数器
            redis_manager.add_yahtzee_bonus(score_data.match_id, score_data.user_id, 0)
        elif yahtzee_bonus_count > 0:
            # 已有过 Yahtzee，再掷出 5 个相同 → 任何计分项都给 100 奖励
            yahtzee_bonus = 100
            redis_manager.add_yahtzee_bonus(score_data.match_id, score_data.user_id, yahtzee_bonus)
    
    # 添加玩家得分（更新已用计分项和总分）
    redis_manager.add_player_score(score_data.match_id, score_data.user_id, score_data.score_type, round_score)
    
    # 获取更新后的玩家数据
    player_data = redis_manager.get_player_data(score_data.match_id, score_data.user_id)
    total_score = player_data["total_score"] if player_data else round_score
    
    # 记录到计分项使用表
    create_match_score_sheet(
        db,
        score_data.match_id,
        score_data.user_id,
        score_data.score_type,
        round_score
    )
    
    update_user_total_score(db, score_data.user_id, round_score)
    
    # 获取用户信息以更新排行榜
    user = get_user_by_id(db, score_data.user_id)
    nickname = user.nickname if user else ""
    
    # 更新排行榜
    redis_manager.update_total_ranking(score_data.user_id, nickname, total_score)
    
    # 广播分数选择给其他玩家
    await manager.broadcast(
        f"match:{score_data.match_id}",
        {
            "type": "score_selected",
            "user_id": score_data.user_id,
            "score_type": score_data.score_type,
            "score": round_score,
            "total_score": total_score,
            "yahtzee_bonus": yahtzee_bonus,
            "is_yahtzee": is_yahtzee
        },
        exclude_user_id=score_data.user_id
    )
    
    # 切换到下一个玩家（使用取余循环）
    room_id = state.get("room_id")
    if not room_id:
        raise HTTPException(status_code=400, detail="房间ID不存在")
    
    room_players = redis_manager.get_room_players(room_id)
    player_count = len(room_players)
    
    if player_count == 0:
        raise HTTPException(status_code=400, detail="房间中没有玩家")
    
    # 获取当前玩家的座位号
    current_seat_no = state.get("current_seat_no", 0)
    
    # 找到当前玩家在列表中的索引
    current_index = next((i for i, p in enumerate(room_players) if p["seat_no"] == current_seat_no), 0)
    
    # 使用取余方法计算下一个玩家索引
    next_index = (current_index + 1) % player_count
    
    # 判断是否完成一轮（回到起始玩家）
    if next_index == 0:
        state["current_round"] = state.get("current_round", 0) + 1
        if state["current_round"] > 13:
            state["status"] = "finished"
            
            # 获取房间信息以获取 game_mode
            room = get_room_by_id(db, room_id)
            game_mode = room.game_mode if room else 1
            
            # 游戏结束，收集所有玩家的最终得分
            player_scores = []
            for player in room_players:
                player_data = redis_manager.get_player_data(score_data.match_id, player["user_id"])
                final_score = player_data["total_score"] if player_data else 0
                
                # 计算上半部分奖励（Upper Section Bonus）
                upper_section_score = get_upper_section_score(db, score_data.match_id, player["user_id"])
                upper_bonus = 35 if upper_section_score >= 63 else 0
                
                # 如果有上半部分奖励，更新总分并写入数据库
                if upper_bonus > 0:
                    # 写入 Redis
                    redis_manager.add_player_score(score_data.match_id, player["user_id"], "upper_bonus", upper_bonus)
                    final_score += upper_bonus
                    
                    # 写入数据库（MatchScoreSheet 表）
                    create_match_score_sheet(
                        db,
                        score_data.match_id,
                        player["user_id"],
                        "upper_bonus",
                        upper_bonus
                    )
                    
                    # 更新用户总经验
                    update_user_total_score(db, player["user_id"], upper_bonus)
                
                player_scores.append({
                    "user_id": player["user_id"],
                    "final_score": final_score,
                    "upper_section_score": upper_section_score,
                    "upper_bonus": upper_bonus
                })
            
            # 按得分降序排序，计算排名
            player_scores.sort(key=lambda x: -x["final_score"])
            for i, ps in enumerate(player_scores):
                rank = i + 1
                is_win = 1 if i == 0 else 0  # 第一名获胜
                
                # 更新游戏战绩表（使用从房间获取的 game_mode）
                create_game_record(
                    db,
                    score_data.match_id,
                    ps["user_id"],
                    ps["final_score"],
                    rank=rank,
                    is_win=is_win,
                    game_mode=game_mode
                )
                
                # 更新用户历史统计表
                update_user_history_stats(db, ps["user_id"], ps["final_score"], is_win)
                
                # 更新用户每日统计表
                update_user_daily_stats(db, ps["user_id"], ps["final_score"], is_win)
            
            # 获取获胜者
            winner_user_id = player_scores[0]["user_id"] if player_scores else None
            
            # 更新对局表信息
            update_match(
                db,
                score_data.match_id,
                match_status=2,           # 1=进行中，2=已结束
                winner_user_id=winner_user_id,
                end_time=datetime.now()
            )
            
            # 广播游戏结束给所有玩家
            await manager.broadcast(
                f"match:{score_data.match_id}",
                {
                    "type": "game_ended",
                    "results": player_scores,
                    "winner": winner_user_id
                }
            )
            
            # 更新对局状态为已结束
            state["status"] = "finished"
            redis_manager.set_match_state(score_data.match_id, state)

            # 缩短对局相关 key 的 TTL，让其快速过期
            player_ids = [p["user_id"] for p in room_players]
            redis_manager.expire_match_keys(score_data.match_id, player_ids)

            return success(SelectScoreResponse(round_score=round_score, total_score=total_score), msg="游戏结束")
    
    next_player = room_players[next_index]
    
    # 通知下一个玩家轮到他了
    await manager.send_to_user(
        next_player["user_id"],
        {
            "type": "your_turn",
            "match_id": score_data.match_id,
            "current_round": state.get("current_round", 1)
        }
    )
    
    # 更新对局状态
    state["current_turn_user_id"] = next_player["user_id"]
    state["current_seat_no"] = next_player["seat_no"]
    state["phase"] = "THROWING"
    state["selectable_scores"] = []
    state["remain_throw_count"] = 3
    state["dice_values"] = []
    state["locked_dice"] = []
    redis_manager.set_match_state(score_data.match_id, state)
    
    return success(SelectScoreResponse(round_score=round_score, total_score=total_score), msg="选择分数成功")

@router.get("/final_score")
async def get_final_score(match_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    records = get_game_records_by_match(db, match_id)
    return success([GameRecordResponse.model_validate(r) for r in records], msg="获取最终成绩成功")

def calculate_score(dice: List[int], score_type: str) -> int:
    dice_sorted = sorted(dice)
    counts = [0] * 7
    for d in dice:
        counts[d] += 1
    
    if score_type == "ones":
        return counts[1] * 1
    elif score_type == "twos":
        return counts[2] * 2
    elif score_type == "threes":
        return counts[3] * 3
    elif score_type == "fours":
        return counts[4] * 4
    elif score_type == "fives":
        return counts[5] * 5
    elif score_type == "sixes":
        return counts[6] * 6
    elif score_type == "three_of_a_kind":
        return sum(dice) if max(counts) >= 3 else 0
    elif score_type == "four_of_a_kind":
        return sum(dice) if max(counts) >= 4 else 0
    elif score_type == "full_house":
        return 25 if (3 in counts and 2 in counts) else 0
    elif score_type == "small_straight":
        return 30 if any(all(x in dice for x in seq) for seq in [[1,2,3,4], [2,3,4,5], [3,4,5,6]]) else 0
    elif score_type == "large_straight":
        return 40 if dice_sorted in [[1,2,3,4,5], [2,3,4,5,6]] else 0
    elif score_type == "yahtzee":
        return 50 if max(counts) == 5 else 0
    elif score_type == "chance":
        return sum(dice)
    return 0
