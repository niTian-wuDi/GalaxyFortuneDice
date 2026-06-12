import json
from typing import List, Optional

class RedisManager:
    def __init__(self, redis_client):
        self.redis = redis_client

    # 房间玩家列表 TTL：24 小时（安全兜底，防止异常未清理的残留 key）
    ROOM_PLAYERS_TTL = 24 * 60 * 60

    # 对局相关 TTL
    MATCH_ACTIVE_TTL = 2 * 60 * 60       # 进行中的对局：2 小时
    MATCH_FINISHED_TTL = 10 * 60          # 已结束的对局：10 分钟（留给客户端拉取最终成绩）

    def set_room_players(self, room_id: int, players: List[dict]):
        key = f"room:{room_id}:players"
        self.redis.set(key, json.dumps(players), ex=self.ROOM_PLAYERS_TTL)

    def get_room_players(self, room_id: int) -> List[dict]:
        key = f"room:{room_id}:players"
        data = self.redis.get(key)
        return json.loads(data) if data else []

    def delete_room_players(self, room_id: int):
        key = f"room:{room_id}:players"
        self.redis.delete(key)

    def set_match_state(self, match_id: int, state: dict):
        key = f"match:{match_id}:state"
        state_copy = state.copy()
        if "dice_values" in state_copy:
            state_copy["dice_values"] = json.dumps(state_copy["dice_values"])
        if "locked_dice" in state_copy:
            state_copy["locked_dice"] = json.dumps(state_copy["locked_dice"])
        if "selectable_scores" in state_copy:
            state_copy["selectable_scores"] = json.dumps(state_copy["selectable_scores"])
        self.redis.hset(key, mapping=state_copy)
        self.redis.expire(key, self.MATCH_ACTIVE_TTL)

    def get_match_state(self, match_id: str) -> Optional[dict]:
        key = f"match:{match_id}:state"
        data = self.redis.hgetall(key)
        if not data:
            return None

        result = {}
        for k, v in data.items():
            k_str = k
            v_str = v

            try:
                result[k_str] = int(v_str)
                continue
            except ValueError:
                pass

            try:
                result[k_str] = json.loads(v_str)
                continue
            except (json.JSONDecodeError, ValueError):
                pass

            result[k_str] = v_str

        return result


    def init_player_data(self, match_id: int, user_id: int):
        key = f"match:{match_id}:player:{user_id}"
        self.redis.hset(key, mapping={
            "dice_values": json.dumps([]),
            "locked_dice": json.dumps([]),
            "used_scores": json.dumps([]),
            "total_score": 0,
            "yahtzee_bonus_count": 0
        })
        self.redis.expire(key, self.MATCH_ACTIVE_TTL)

    def get_player_data(self, match_id: int, user_id: int) -> Optional[dict]:
        key = f"match:{match_id}:player:{user_id}"
        data = self.redis.hgetall(key)
        if not data:
            return None

        return {
            "dice_values": json.loads(data.get("dice_values", "[]")),
            "locked_dice": json.loads(data.get("locked_dice", "[]")),
            "used_scores": json.loads(data.get("used_scores", "[]")),
            "total_score": int(data.get("total_score", "0")),
            "yahtzee_bonus_count": int(data.get("yahtzee_bonus_count", "0"))
        }

    def update_player_dice(self, match_id: int, user_id: int, dice_values: List[int], locked_dice: List[bool]):
        key = f"match:{match_id}:player:{user_id}"
        self.redis.hset(key, mapping={
            "dice_values": json.dumps(dice_values),
            "locked_dice": json.dumps(locked_dice)
        })
        self.redis.expire(key, self.MATCH_ACTIVE_TTL)

    def add_player_score(self, match_id: int, user_id: int, score_type: str, score: int):
        key = f"match:{match_id}:player:{user_id}"

        data = self.get_player_data(match_id, user_id)
        if not data:
            return

        used_scores = data["used_scores"]
        if score_type not in used_scores:
            used_scores.append(score_type)

        total_score = data["total_score"] + score

        self.redis.hset(key, mapping={
            "used_scores": json.dumps(used_scores),
            "total_score": total_score
        })
        self.redis.expire(key, self.MATCH_ACTIVE_TTL)

    def add_yahtzee_bonus(self, match_id: int, user_id: int, bonus: int):
        key = f"match:{match_id}:player:{user_id}"

        data = self.get_player_data(match_id, user_id)
        if not data:
            return

        total_score = data["total_score"] + bonus
        yahtzee_bonus_count = data["yahtzee_bonus_count"] + 1

        self.redis.hset(key, mapping={
            "total_score": total_score,
            "yahtzee_bonus_count": yahtzee_bonus_count
        })
        self.redis.expire(key, self.MATCH_ACTIVE_TTL)

    def get_upper_section_score(self, match_id: int, user_id: int) -> int:
        key = f"match:{match_id}:player:{user_id}"
        data = self.redis.hgetall(key)
        if not data:
            return 0

        used_scores = json.loads(data.get("used_scores", "[]"))
        upper_types = ["ones", "twos", "threes", "fours", "fives", "sixes"]

        return 0

    def expire_match_keys(self, match_id: int, player_ids: List[int]):
        """对局结算后，缩短所有对局相关 key 的 TTL，让其快速过期"""
        self.redis.expire(f"match:{match_id}:state", self.MATCH_FINISHED_TTL)
        for user_id in player_ids:
            self.redis.expire(f"match:{match_id}:player:{user_id}", self.MATCH_FINISHED_TTL)

    def update_total_ranking(self, user_id: int, nickname: str, score: int):
        key = "ranking:total"
        member = f"{user_id}:{nickname}"
        self.redis.zadd(key, {member: score})

    def get_total_ranking(self, limit: int, offset: int = 0) -> List[tuple]:
        key = "ranking:total"
        return self.redis.zrevrange(key, offset, offset + limit - 1, withscores=True)

    # 每日排行榜 TTL：3 天（过期日期的排行榜无业务价值）
    DAILY_RANKING_TTL = 3 * 24 * 60 * 60

    def update_daily_ranking(self, date: str, user_id: int, nickname: str, score: int):
        key = f"ranking:daily:{date}"
        member = f"{user_id}:{nickname}"
        self.redis.zadd(key, {member: score})
        self.redis.expire(key, self.DAILY_RANKING_TTL)

    def get_daily_ranking(self, date: str, limit: int, offset: int = 0) -> List[tuple]:
        key = f"ranking:daily:{date}"
        return self.redis.zrevrange(key, offset, offset + limit - 1, withscores=True)
