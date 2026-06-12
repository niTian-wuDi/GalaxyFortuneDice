from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from config.db_config import get_db
from schemas.user import UserCreate, UserLogin, AuthResponse, UserInfoResponse
from crud.user import get_user_by_phone, create_user, create_guest_user, get_user_info_with_stats
from utils.security import verify_password, create_access_token
from utils.re import validate_login_data, validate_register_data
from utils.response import success
from utils.security import get_current_user
from models.user import User
router = APIRouter(prefix="/api/user", tags=["用户"])

# 登录接口
@router.post("/login")
async def login(user_data: UserLogin, db: Session = Depends(get_db)):
    validate_login_data(user_data)

    user = get_user_by_phone(db, user_data.phone)
    if not user or not verify_password(user_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="手机号或密码错误"
        )
    access_token = create_access_token(data={"sub": str(user.id)})
    return success(AuthResponse(token=access_token), msg="登录成功")

# 注册接口
@router.post("/register")
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
    validate_register_data(user_data)

    if user_data.phone and get_user_by_phone(db, user_data.phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="手机号已被注册"
        )
    user = create_user(db, user_data)
    access_token = create_access_token(data={"sub": str(user.id)})
    return success(AuthResponse(token=access_token), msg="注册成功")

@router.post("/guest")
async def guest_login(db: Session = Depends(get_db)):
    user = create_guest_user(db)
    access_token = create_access_token(data={"sub": str(user.id)})
    return success(AuthResponse(token=access_token), msg="登录成功")

# 获取当前用户详细信息
@router.get("/info")
async def get_user_info(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_info = get_user_info_with_stats(db, current_user.id)
    if not user_info:
        raise HTTPException(status_code=404, detail="用户不存在")
    return success(UserInfoResponse(**user_info), msg="获取用户信息成功")
