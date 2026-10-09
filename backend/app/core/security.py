from datetime import datetime, timedelta, timezone
import hashlib, hmac, os
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.models.models import User, Role

bearer = HTTPBearer(auto_error=False)

PASSWORD_ITERATIONS=600000

def hash_password(password: str) -> str:
    salt=os.urandom(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,PASSWORD_ITERATIONS)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}'

def verify_password(password: str, encoded: str) -> bool:
    try:
        if encoded.startswith('pbkdf2_sha256$'):
            _, iterations, salt_hex, digest_hex = encoded.split('$', 3)
            salt=bytes.fromhex(salt_hex)
            check=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,int(iterations))
            return hmac.compare_digest(check.hex(),digest_hex)
        salt_hex,digest_hex=encoded.split(':',1)
        salt=bytes.fromhex(salt_hex)
        check=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310000)
        return hmac.compare_digest(check.hex(),digest_hex)
    except Exception:
        return False

def create_access_token(subject: str):
    exp=datetime.now(timezone.utc)+timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({'sub':subject,'exp':exp},settings.secret_key,algorithm='HS256')

def decode_access_token(token: str):
    try: return jwt.decode(token,settings.secret_key,algorithms=['HS256']).get('sub')
    except Exception: return None

def current_user(credentials: HTTPAuthorizationCredentials|None=Depends(bearer), db: Session=Depends(get_db)) -> User:
    if not credentials:
        raise HTTPException(401,'Authentication required.',headers={'WWW-Authenticate':'Bearer'})
    uid=decode_access_token(credentials.credentials)
    if not uid or not uid.isdigit(): raise HTTPException(401,'Invalid or expired session.')
    user=db.get(User,int(uid))
    if not user or not user.is_active: raise HTTPException(401,'Invalid or inactive user.')
    return user

def require_roles(*roles):
    def dependency(user: User=Depends(current_user), db: Session=Depends(get_db)) -> User:
        role=db.get(Role,user.role_id) if user.role_id else None
        if not role or role.name not in roles:
            raise HTTPException(403,'You do not have permission for this operation.')
        return user
    return dependency
