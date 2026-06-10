from sqlalchemy.orm import Session
from models.user import User
from models.stats import UserHistoryStats, UserDailyStats
from schemas.user import UserCreate
from utils.security import get_password_hash
import uuid
from typing import Optional
from datetime import date
from sqlalchemy.sql import func

def get_user_by_phone(db: Session, phone: str) -> Optional[User]:
    return db.query(User).filter(User.phone == phone).first()

def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()

def get_user_info_with_stats(db: Session, user_id: int) -> Optional[dict]:
    """查询用户信息并关联历史统计，返回合并后的字典"""
    user = get_user_by_id(db, user_id)
    if not user:
        return None

    stats = db.query(UserHistoryStats).filter(UserHistoryStats.user_id == user_id).first()

    return {
        "id": user.id,
        "phone": user.phone,
        "nickname": user.nickname,
        "avatar": user.avatar,
        "exp": user.exp,
        "create_time": user.create_time,
        "total_games": stats.total_games if stats else 0,
        "total_wins": stats.total_wins if stats else 0,
        "max_score": stats.max_score if stats else 0,
    }

def create_user(db: Session, user: UserCreate) -> User:
    hashed_password = None
    if user.password:
        hashed_password = get_password_hash(user.password)
    db_user = User(
        phone=user.phone,
        nickname=user.nickname,
        password=hashed_password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def create_guest_user(db: Session) -> User:
    guest_nickname = f"游客_{uuid.uuid4().hex[:8]}"
    db_user = User(
        nickname=guest_nickname
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user_total_score(db: Session, user_id: int, score: int):
    user = get_user_by_id(db, user_id)
    if user:
        user.exp += score
        db.commit()

def update_user_history_stats(db: Session, user_id: int, final_score: int, is_win: int):
    stats = db.query(UserHistoryStats).filter(UserHistoryStats.user_id == user_id).first()
    
    if stats:
        stats.total_games += 1
        if is_win:
            stats.total_wins += 1
        if final_score > stats.max_score:
            stats.max_score = final_score
    else:
        stats = UserHistoryStats(
            user_id=user_id,
            total_games=1,
            total_wins=1 if is_win else 0,
            max_score=final_score
        )
        db.add(stats)
    
    db.commit()
    db.refresh(stats)
    return stats

def update_user_daily_stats(db: Session, user_id: int, final_score: int, is_win: int, stat_date: date = None):
    if stat_date is None:
        stat_date = date.today()
    
    stats = db.query(UserDailyStats).filter(
        UserDailyStats.user_id == user_id,
        UserDailyStats.stat_date == stat_date
    ).first()
    
    if stats:
        stats.daily_games += 1
        if is_win:
            stats.daily_wins += 1
        if final_score > stats.daily_max_score:
            stats.daily_max_score = final_score
    else:
        stats = UserDailyStats(
            user_id=user_id,
            stat_date=stat_date,
            daily_games=1,
            daily_wins=1 if is_win else 0,
            daily_max_score=final_score
        )
        db.add(stats)
    
    db.commit()
    db.refresh(stats)
    return stats
