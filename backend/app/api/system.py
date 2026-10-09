from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.models.models import User, Role, Product, Customer, Supplier, BankAccount, Unit, ProductUnitConversion, EmptyContainerType, EmptyContainerMovement, Account, JournalEntry, JournalLine, AuditLog, BusinessProfile, EmployeeProfile, Sale, Purchase, Expense, CashTransaction, BankTransaction, CustomerPayment, SupplierPayment, StockMovement
from app.schemas.common import CustomerCreate, SupplierCreate, BankCreate, UnitCreatePayload, ContainerCreate, OpeningBalancesApply, OpeningBalanceSet, ProductUpdate, ConversionCreate
from app.core.security import hash_password, verify_password, create_access_token, decode_access_token, current_user, require_roles
from app.core.industry_profiles import INDUSTRY_PROFILES, normalize_industry, feature_enabled
from app.services.accounting import post_journal, account
from decimal import Decimal
from datetime import datetime, timedelta, timezone, date
import json
import re
import unicodedata
import secrets
from app.core.config import settings
from app.services.mailboxes import create_mailbox as provision_company_mailbox, change_mailbox_password as change_company_mailbox_password, provider_status as company_mail_provider_status, MailboxProvisionError

router = APIRouter(prefix='/api')

class SetupCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    business_name: str = Field(min_length=2, max_length=180)
    phone: str|None = Field(default=None, max_length=50)
    address: str|None = Field(default=None, max_length=255)
    tin: str|None = Field(default=None, max_length=80)
    vat_number: str|None = Field(default=None, max_length=80)
    currency: str = Field(default='TZS', min_length=3, max_length=10)
    industry_profile: str = Field(default='retail', pattern='^(retail|wholesale|restaurant|pharmacy|electronics|hardware|services|manufacturing|beverage|other)$')

class BusinessProfileUpdate(BaseModel):
    business_name: str = Field(min_length=2, max_length=180)
    owner_name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    phone: str|None = Field(default=None, max_length=50)
    address: str|None = Field(default=None, max_length=255)
    tin: str|None = Field(default=None, max_length=80)
    vat_number: str|None = Field(default=None, max_length=80)
    currency: str = Field(default='TZS', min_length=3, max_length=10)
    industry_profile: str = Field(default='retail', pattern='^(retail|wholesale|restaurant|pharmacy|electronics|hardware|services|manufacturing|beverage|other)$')

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

def seed_roles(db):
    for name in ('owner','admin','manager','salesperson','storekeeper','accountant','cashier'):
        if not db.query(Role).filter(Role.name == name).first():
            db.add(Role(name=name))
    db.commit()

def setup_status(db: Session):
    return {'setup_required': db.query(User).count() == 0}

@router.get('/setup/status')
def status(db: Session = Depends(get_db)):
    return setup_status(db)

@router.post('/setup')
def setup(payload: SetupCreate, db: Session = Depends(get_db)):
    if db.query(User).count():
        raise HTTPException(409, 'Initial setup has already been completed.')
    seed_roles(db)
    owner = db.query(Role).filter(Role.name == 'owner').first()
    user = User(name=payload.name.strip(), email=payload.email.lower(), password_hash=hash_password(payload.password), role_id=owner.id, is_active=True)
    profile = BusinessProfile(id=1, business_name=payload.business_name.strip(), owner_name=payload.name.strip(), email=payload.email.lower(), phone=(payload.phone or '').strip() or None, address=(payload.address or '').strip() or None, tin=(payload.tin or '').strip() or None, vat_number=(payload.vat_number or '').strip() or None, currency=payload.currency.upper().strip(), industry_profile=normalize_industry(payload.industry_profile), onboarding_completed=False)
    db.add(user); db.flush()
    owner_employee=EmployeeProfile(user_id=user.id,full_name=user.name,company_email=user.email,is_active=True,employment_type='full_time',mailbox_status='address_only')
    db.add(owner_employee); db.flush()
    owner_employee.employee_number=_suggest_employee_number(db, owner_employee.id)
    db.add(profile); db.commit(); db.refresh(user)
    return {'ok': True, 'message': f'{profile.business_name} is ready. You can now sign in.', 'business': {'business_name': profile.business_name, 'owner_name': profile.owner_name, 'email': profile.email, 'phone': profile.phone, 'address': profile.address, 'tin': profile.tin, 'vat_number': profile.vat_number, 'currency': profile.currency, 'industry_profile': profile.industry_profile}}

@router.get('/business-profile')
def business_profile(db: Session = Depends(get_db), _: User = Depends(current_user)):
    p = db.get(BusinessProfile, 1)
    if not p:
        raise HTTPException(404, 'Business profile has not been configured yet.')
    return {'business_name':p.business_name,'owner_name':p.owner_name,'email':p.email,'phone':p.phone,'address':p.address,'tin':p.tin,'vat_number':p.vat_number,'currency':p.currency,'industry_profile':normalize_industry(getattr(p,'industry_profile',None)),'industry_label':INDUSTRY_PROFILES[normalize_industry(getattr(p,'industry_profile',None))]['label'],'features':sorted(INDUSTRY_PROFILES[normalize_industry(getattr(p,'industry_profile',None))]['features']),'onboarding_completed':bool(getattr(p,'onboarding_completed',False))}

@router.put('/business-profile')
def update_business_profile(payload: BusinessProfileUpdate, db: Session = Depends(get_db), actor: User = Depends(require_roles('owner','admin'))):
    p = db.get(BusinessProfile, 1)
    if not p:
        p = BusinessProfile(id=1)
        db.add(p)
    new_email=payload.email.lower().strip()
    if actor and _role(db,actor) and _role(db,actor).name!='owner' and (new_email != (p.email or '').lower() or payload.owner_name.strip() != (p.owner_name or '').strip()):
        raise HTTPException(403,'Only the owner can change the owner name or login email.')
    duplicate=db.query(User).filter(func.lower(User.email)==new_email, User.id!=actor.id).first()
    if duplicate: raise HTTPException(409,'That email is already used by another user.')
    p.business_name=payload.business_name.strip(); p.owner_name=payload.owner_name.strip(); p.email=new_email; p.phone=(payload.phone or '').strip() or None; p.address=(payload.address or '').strip() or None; p.tin=(payload.tin or '').strip() or None; p.vat_number=(payload.vat_number or '').strip() or None; p.currency=payload.currency.upper().strip(); p.industry_profile=normalize_industry(payload.industry_profile)
    if _role(db,actor) and _role(db,actor).name=='owner':
        actor.name=p.owner_name; actor.email=p.email
    db.commit(); db.refresh(p)
    return {'business_name':p.business_name,'owner_name':p.owner_name,'email':p.email,'phone':p.phone,'address':p.address,'tin':p.tin,'vat_number':p.vat_number,'currency':p.currency,'industry_profile':p.industry_profile,'industry_label':INDUSTRY_PROFILES[normalize_industry(p.industry_profile)]['label'],'features':sorted(INDUSTRY_PROFILES[normalize_industry(p.industry_profile)]['features']),'onboarding_completed':bool(p.onboarding_completed)}

@router.post('/auth/login')
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    now=datetime.now(timezone.utc).replace(tzinfo=None)
    user = db.query(User).filter(func.lower(User.email) == payload.email.lower()).first()
    # Five failed attempts trigger a temporary 15-minute lockout. This is deliberately
    # stored server-side so restarting the desktop client does not reset the protection.
    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(429, 'Too many failed login attempts. Try again later.')
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        if user and user.is_active:
            user.failed_login_attempts=(user.failed_login_attempts or 0)+1
            if user.failed_login_attempts >= 5:
                user.locked_until=now+timedelta(minutes=15)
                user.failed_login_attempts=0
            db.commit()
        raise HTTPException(401, 'Invalid email or password.')
    role = db.get(Role, user.role_id) if user.role_id else None
    if role is None:
        raise HTTPException(403, 'Your account has no valid role assigned. Ask an owner or administrator to correct the account.')
    user.last_login_at=now
    user.failed_login_attempts=0
    user.locked_until=None
    db.commit()
    return {'access_token': create_access_token(str(user.id)), 'user': {'id': user.id, 'name': user.name, 'email': user.email, 'role': role.name if role else None}}

@router.get('/auth/me')
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    role=db.get(Role,user.role_id)
    return {'id':user.id,'name':user.name,'email':user.email,'role':role.name if role else None}

@router.post('/auth/change-password')
def change_password(payload: dict, db: Session=Depends(get_db), user: User=Depends(current_user)):
    current=str(payload.get('current_password','')); new=str(payload.get('new_password',''))
    if len(new)<8: raise HTTPException(400,'New password must be at least 8 characters.')
    if not verify_password(current,user.password_hash): raise HTTPException(400,'Current password is incorrect.')
    user.password_hash=hash_password(new); user.failed_login_attempts=0; user.locked_until=None
    db.add(AuditLog(user_id=user.id,action='password_changed',entity_type='user',entity_id=user.id)); db.commit()
    return {'status':'ok'}


@router.get('/industry-profiles')
def industry_profiles(_: User = Depends(current_user)):
    return [{'key': key, 'label': value['label'], 'description': value['description'], 'features': sorted(value['features'])} for key, value in INDUSTRY_PROFILES.items()]

@router.get('/onboarding')
def onboarding_progress(db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = db.get(BusinessProfile, 1)
    products = db.query(Product).filter(Product.is_active == True).count() if p else 0
    opening_stock = db.query(Product).filter(Product.is_active == True, Product.current_stock > 0).count() if p else 0
    banks = db.query(BankAccount).count() if p else 0
    steps = [
        {'key': 'business_profile', 'title': 'Set up your business', 'done': bool(p and p.business_name and p.industry_profile)},
        {'key': 'products', 'title': 'Add your first products or services', 'done': products > 0},
        {'key': 'opening_stock', 'title': 'Set opening stock', 'done': opening_stock > 0 or (p and normalize_industry(p.industry_profile) == 'services')},
        {'key': 'finance', 'title': 'Configure cash or bank', 'done': banks > 0},
        {'key': 'first_sale', 'title': 'Post your first sale', 'done': db.query(Sale).count() > 0 if p else False},
    ]
    completed = sum(1 for step in steps if step['done'])
    if p and p.onboarding_completed != (completed == len(steps)):
        p.onboarding_completed = completed == len(steps)
        db.commit()
    return {'industry_profile': normalize_industry(getattr(p, 'industry_profile', None) if p else None), 'industry_label': INDUSTRY_PROFILES[normalize_industry(getattr(p, 'industry_profile', None) if p else None)]['label'], 'completed': completed, 'total': len(steps), 'percentage': round(completed / len(steps) * 100) if steps else 100, 'steps': steps}

@router.get('/users/roles')
def list_assignable_roles(db: Session = Depends(get_db), actor: User = Depends(require_roles('owner','admin'))):
    actor_role = _role(db, actor)
    rows = db.query(Role).order_by(Role.name).all()
    allowed = rows if actor_role and actor_role.name == 'owner' else [r for r in rows if r.name not in ('owner','admin')]
    return [{'name': r.name, 'can_login': True} for r in allowed]

@router.get('/users')
def list_users(db: Session = Depends(get_db), _: User = Depends(require_roles('owner','admin'))):
    rows=[]
    for u in db.query(User).order_by(User.name).all():
        role=db.get(Role,u.role_id)
        ep=db.query(EmployeeProfile).filter(EmployeeProfile.user_id==u.id).first()
        rows.append({'id':u.id,'name':u.name,'email':u.email,'role':role.name if role else None,
                     'is_active':u.is_active,'last_login_at':u.last_login_at.isoformat() if u.last_login_at else None,
                     'employee_number':ep.employee_number if ep else None,'phone':ep.phone if ep else None,
                     'department':ep.department if ep else None,'job_title':ep.job_title if ep else None,
                     'employment_type':ep.employment_type if ep else 'full_time',
                     'hire_date':ep.hire_date.isoformat() if ep and ep.hire_date else None,
                     'termination_date':ep.termination_date.isoformat() if ep and ep.termination_date else None,
                     'personal_email':ep.personal_email if ep else None,
                     'mailbox_status':ep.mailbox_status if ep else 'not_configured',
                     'mailbox_provider':ep.mailbox_provider if ep else None,
                     'mailbox_last_error':ep.mailbox_last_error if ep else None})
    return rows

class UserCreate(BaseModel):
    name: str = Field(min_length=2,max_length=150)
    email: EmailStr | None = None
    generate_company_email: bool = False
    password: str = Field(min_length=8,max_length=128)
    role: str = Field(min_length=2,max_length=80)
    employee_number: str | None = Field(default=None,max_length=40)
    phone: str | None = Field(default=None,max_length=50)
    department: str | None = Field(default=None,max_length=100)
    job_title: str | None = Field(default=None,max_length=100)
    employment_type: str = Field(default='full_time',pattern='^(full_time|part_time|contract|temporary|intern|casual)$')
    hire_date: date | None = None
    personal_email: EmailStr | None = None
    notes: str | None = Field(default=None,max_length=2000)
    create_mailbox: bool = False
    mailbox_password: str | None = Field(default=None,min_length=12,max_length=128)
    mailbox_quota_mb: int | None = Field(default=None,ge=0,le=1000000)

    @model_validator(mode='after')
    def validate_email_choice(self):
        if not self.generate_company_email and not self.email:
            raise ValueError('Enter an email address or choose company email generation.')
        if self.create_mailbox and not self.mailbox_password:
            raise ValueError('A separate mailbox password of at least 12 characters is required.')
        return self

class UserUpdate(BaseModel):
    name: str | None = Field(default=None,min_length=2,max_length=150)
    email: EmailStr | None = None
    role: str | None = Field(default=None,min_length=2,max_length=80)
    employee_number: str | None = Field(default=None,max_length=40)
    phone: str | None = Field(default=None,max_length=50)
    department: str | None = Field(default=None,max_length=100)
    job_title: str | None = Field(default=None,max_length=100)
    employment_type: str | None = Field(default=None,pattern='^(full_time|part_time|contract|temporary|intern|casual)$')
    hire_date: date | None = None
    personal_email: EmailStr | None = None
    notes: str | None = Field(default=None,max_length=2000)

class UserPasswordReset(BaseModel):
    password: str = Field(min_length=8,max_length=128)

class EmailDomainUpdate(BaseModel):
    domain: str = Field(min_length=4,max_length=255)

class MailboxCreate(BaseModel):
    password: str = Field(min_length=12,max_length=128)
    quota_mb: int | None = Field(default=None,ge=0,le=1000000)

class MailboxPasswordUpdate(BaseModel):
    password: str = Field(min_length=12,max_length=128)

class EmployeeCreate(BaseModel):
    full_name: str = Field(min_length=2,max_length=150)
    employee_number: str | None = Field(default=None,max_length=40)
    company_email: EmailStr | None = None
    generate_company_email: bool = False
    phone: str | None = Field(default=None,max_length=50)
    department: str | None = Field(default=None,max_length=100)
    job_title: str | None = Field(default=None,max_length=100)
    employment_type: str = Field(default='full_time',pattern='^(full_time|part_time|contract|temporary|intern|casual)$')
    hire_date: date | None = None
    personal_email: EmailStr | None = None
    notes: str | None = Field(default=None,max_length=2000)
    create_login: bool = False
    login_email: EmailStr | None = None
    login_password: str | None = Field(default=None,min_length=8,max_length=128)
    login_role: str | None = Field(default=None,min_length=2,max_length=80)

    @model_validator(mode='after')
    def validate_optional_login(self):
        if self.create_login:
            if not self.login_password:
                raise ValueError('Set a temporary CODEZONE login password when enabling login access.')
            if not self.login_role:
                raise ValueError('Choose a CODEZONE role when enabling login access.')
            if not self.login_email and not self.company_email and not self.generate_company_email:
                raise ValueError('Login email is required unless a company email will be generated.')
        return self

class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None,min_length=2,max_length=150)
    employee_number: str | None = Field(default=None,max_length=40)
    company_email: EmailStr | None = None
    phone: str | None = Field(default=None,max_length=50)
    department: str | None = Field(default=None,max_length=100)
    job_title: str | None = Field(default=None,max_length=100)
    employment_type: str | None = Field(default=None,pattern='^(full_time|part_time|contract|temporary|intern|casual)$')
    hire_date: date | None = None
    personal_email: EmailStr | None = None
    notes: str | None = Field(default=None,max_length=2000)

def _role(db, user):
    return db.get(Role,user.role_id) if user and user.role_id else None

def _suggest_employee_number(db, preferred_id: int | None = None):
    """Return the first unused human-readable employee number without overwriting custom IDs."""
    candidate_id=max(int(preferred_id or 1),1)
    for _ in range(100000):
        candidate=f'EMP-{candidate_id:05d}'
        if not db.query(EmployeeProfile).filter(func.lower(EmployeeProfile.employee_number)==candidate.lower()).first():
            return candidate
        candidate_id+=1
    raise HTTPException(409,'Unable to allocate a unique employee number. Contact an administrator.')


def _employee_profile(db, user_id, create=False):
    profile=db.query(EmployeeProfile).filter(EmployeeProfile.user_id==user_id).first()
    if profile is None and create:
        u=db.get(User,user_id)
        profile=EmployeeProfile(user_id=user_id,full_name=u.name if u else None,company_email=u.email if u else None,
            is_active=bool(u.is_active) if u else True,employment_type='full_time',mailbox_status='address_only')
        db.add(profile); db.flush()
        profile.employee_number=_suggest_employee_number(db,profile.id)
    return profile

def _company_email_domain(db):
    profile=db.get(BusinessProfile,1)
    domain=(getattr(profile,'email_domain',None) if profile else None) or getattr(settings,'email_domain','') or ''
    return domain.strip().lower().lstrip('@')

def _normalise_local_part(name: str) -> str:
    value=unicodedata.normalize('NFKD',(name or '').strip()).encode('ascii','ignore').decode('ascii').lower()
    value=re.sub(r'[^a-z0-9]+','.',value).strip('.')
    value=re.sub(r'\.+','.',value)
    if not value:
        raise HTTPException(400,'Enter a name with at least one English letter or number to generate an email address.')
    return value[:60].strip('.')

def _suggest_company_email(db, name: str, exclude_user_id: int|None=None):
    domain=_company_email_domain(db)
    if not domain or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain):
        raise HTTPException(400,'Set a valid company email domain first (for example, yourcompany.co.tz).')
    local=_normalise_local_part(name)
    candidate=f'{local}@{domain}'; suffix=2
    while (db.query(User).filter(func.lower(User.email)==candidate.lower(), *([User.id!=exclude_user_id] if exclude_user_id else [])).first()
           or db.query(EmployeeProfile).filter(func.lower(EmployeeProfile.company_email)==candidate.lower()).first()):
        candidate=f'{local}{suffix}@{domain}'; suffix+=1
        if suffix>9999: raise HTTPException(409,'Unable to generate a unique company email address.')
    return candidate

@router.get('/users/email-settings')
def get_email_settings(db: Session=Depends(get_db), _: User=Depends(require_roles('owner','admin'))):
    provider=company_mail_provider_status()
    return {'domain':_company_email_domain(db),'provider':provider['provider'],'provider_ready':provider['ready'],
            'provider_label':provider['label'],'address_generation_ready':bool(_company_email_domain(db))}

@router.put('/users/email-settings')
def set_email_settings(payload: EmailDomainUpdate, db: Session=Depends(get_db), actor: User=Depends(require_roles('owner','admin'))):
    domain=payload.domain.strip().lower().lstrip('@')
    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain):
        raise HTTPException(400,'Enter a valid domain only, such as yourcompany.co.tz (do not include @ or a mailbox name).')
    profile=db.get(BusinessProfile,1)
    if not profile: raise HTTPException(409,'Complete business setup before configuring company emails.')
    before=profile.email_domain
    profile.email_domain=domain
    db.add(AuditLog(user_id=actor.id,action='company_email_domain_updated',entity_type='business_profile',entity_id=profile.id,
                    before_json=json.dumps({'email_domain':before}),after_json=json.dumps({'email_domain':domain})))
    db.commit()
    provider=company_mail_provider_status()
    return {'domain':domain,'provider':provider['provider'],'provider_ready':provider['ready'],'provider_label':provider['label'],'address_generation_ready':True}

@router.get('/users/suggest-email')
def suggest_company_email(name: str, db: Session=Depends(get_db), _: User=Depends(require_roles('owner','admin'))):
    if len(name.strip()) < 2: raise HTTPException(400,'Enter the employee name first.')
    return {'email':_suggest_company_email(db,name)}

@router.post('/users')
def create_user(payload: UserCreate, db: Session = Depends(get_db), actor: User = Depends(require_roles('owner','admin'))):
    actor_role=db.get(Role,actor.role_id) if actor.role_id else None
    role=db.query(Role).filter(Role.name==payload.role).first()
    if not role: raise HTTPException(400,'Unknown role.')
    if payload.role in ('owner','admin') and (not actor_role or actor_role.name!='owner'):
        raise HTTPException(403,'Only the owner can create owner/admin accounts.')
    email=_suggest_company_email(db,payload.name) if payload.generate_company_email else str(payload.email).strip().lower()
    if db.query(User).filter(func.lower(User.email)==email.lower()).first(): raise HTTPException(409,'Email already exists.')
    if payload.create_mailbox:
        if not company_mail_provider_status()['ready']:
            raise HTTPException(400,'A real email provider is not configured. Configure cPanel UAPI in the private CODEZONE config.env file before selecting mailbox creation.')
        if email.rsplit('@',1)[-1].lower()!=_company_email_domain(db):
            raise HTTPException(400,'The mailbox address must use the configured company domain.')
        if len(payload.mailbox_password or '') < 12:
            raise HTTPException(400,'Mailbox password must be at least 12 characters.')
    user=User(name=payload.name.strip(),email=email,password_hash=hash_password(payload.password),role_id=role.id,is_active=True)
    db.add(user)
    try:
        db.flush()
        number=(payload.employee_number or '').strip() or None
        if number and db.query(EmployeeProfile).filter(func.lower(EmployeeProfile.employee_number)==number.lower()).first():
            raise HTTPException(409,'Employee number already exists.')
        ep=EmployeeProfile(user_id=user.id,full_name=user.name,company_email=email,is_active=True,employee_number=number,phone=(payload.phone or '').strip() or None,
            department=(payload.department or '').strip() or None,job_title=(payload.job_title or '').strip() or None,
            employment_type=payload.employment_type,hire_date=payload.hire_date,personal_email=str(payload.personal_email).lower() if payload.personal_email else None,
            notes=(payload.notes or '').strip() or None,mailbox_status='pending' if payload.create_mailbox else 'address_only',
            mailbox_provider='cpanel' if payload.create_mailbox else None)
        db.add(ep); db.flush()
        if not ep.employee_number: ep.employee_number=_suggest_employee_number(db,ep.id)
        number=ep.employee_number
        db.add(AuditLog(user_id=actor.id,action='employee_created',entity_type='user',entity_id=user.id,
            after_json=json.dumps({'name':user.name,'email':user.email,'role':role.name,'employee_number':number,'department':ep.department,'job_title':ep.job_title})))
        db.commit(); db.refresh(user); db.refresh(ep)
    except HTTPException:
        db.rollback(); raise
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409,'Email address or employee number already exists.') from exc
    if payload.create_mailbox:
        try:
            provider_result=provision_company_mailbox(user.email,payload.mailbox_password or '',payload.mailbox_quota_mb)
            ep.mailbox_status='provisioned'; ep.mailbox_provider=provider_result['provider']; ep.mailbox_last_error=None
            db.add(AuditLog(user_id=actor.id,action='company_mailbox_provisioned',entity_type='user',entity_id=user.id,after_json=json.dumps({'email':user.email,'provider':'cpanel'})))
            db.commit()
        except MailboxProvisionError as exc:
            ep.mailbox_status='failed'; ep.mailbox_provider='cpanel'; ep.mailbox_last_error=str(exc)[:250]
            db.add(AuditLog(user_id=actor.id,action='company_mailbox_provision_failed',entity_type='user',entity_id=user.id,after_json=json.dumps({'email':user.email,'reason':str(exc)[:200]})))
            db.commit()
    return {'id':user.id,'name':user.name,'email':user.email,'role':role.name,'is_active':True,'employee_number':ep.employee_number,
            'mailbox_status':ep.mailbox_status,'mailbox_provider':ep.mailbox_provider,'mailbox_last_error':ep.mailbox_last_error,
            'message':'Employee registered. Company email address created.' if ep.mailbox_status=='address_only' else ('Employee registered and mailbox provisioned.' if ep.mailbox_status=='provisioned' else 'Employee registered, but mailbox provisioning failed. Retry it from People → Employees.')}

@router.put('/users/{user_id}')
def update_user(user_id:int,payload:UserUpdate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    u=db.get(User,user_id)
    if not u: raise HTTPException(404,'User not found.')
    ar,tr=_can_manage(actor,u,db)
    ep=_employee_profile(db,u.id,create=True)
    before={'name':u.name,'email':u.email,'role':tr.name,'department':ep.department,'job_title':ep.job_title,'employee_number':ep.employee_number}
    if payload.email:
        email=payload.email.lower()
        if ep.mailbox_status=='provisioned' and email!=u.email.lower():
            raise HTTPException(400,'This employee has a provisioned mailbox. Create/migrate the mailbox with the email provider before changing its address.')
        if db.query(User).filter(func.lower(User.email)==email,User.id!=u.id).first(): raise HTTPException(409,'Email already exists.')
        u.email=email
    if payload.name: u.name=payload.name.strip()
    if payload.role and payload.role!=tr.name:
        new_role=db.query(Role).filter(Role.name==payload.role).first()
        if not new_role: raise HTTPException(400,'Unknown role.')
        if ar.name!='owner' and new_role.name in ('owner','admin'): raise HTTPException(403,'Only the owner can assign owner/admin roles.')
        if tr.name=='owner' and new_role.name!='owner':
            owners=db.query(User).join(Role,Role.id==User.role_id).filter(Role.name=='owner',User.is_active==True).count()
            if owners<=1: raise HTTPException(400,'The last active owner cannot lose the owner role.')
        u.role_id=new_role.id; tr=new_role
    for field in ('employee_number','phone','department','job_title','employment_type','hire_date','notes'):
        value=getattr(payload,field)
        if value is not None:
            if field in ('employee_number','phone','department','job_title','notes'):
                value=value.strip() or None
            setattr(ep,field,value)
    if payload.personal_email is not None: ep.personal_email=str(payload.personal_email).lower()
    after={'name':u.name,'email':u.email,'role':tr.name,'department':ep.department,'job_title':ep.job_title,'employee_number':ep.employee_number}
    db.add(AuditLog(user_id=actor.id,action='employee_updated',entity_type='user',entity_id=u.id,before_json=json.dumps(before),after_json=json.dumps(after)))
    try:
        db.commit(); db.refresh(u); db.refresh(ep)
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409,'Email address or employee number already exists.') from exc
    return {'id':u.id,'name':u.name,'email':u.email,'role':tr.name,'is_active':u.is_active,'employee_number':ep.employee_number,
            'phone':ep.phone,'department':ep.department,'job_title':ep.job_title,'employment_type':ep.employment_type,
            'hire_date':ep.hire_date.isoformat() if ep.hire_date else None,'personal_email':ep.personal_email,
            'mailbox_status':ep.mailbox_status,'last_login_at':u.last_login_at.isoformat() if u.last_login_at else None}

@router.post('/users/{user_id}/reset-password')
def reset_user_password(user_id:int,payload:UserPasswordReset,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    u=db.get(User,user_id)
    if not u: raise HTTPException(404,'User not found.')
    ar,tr=_can_manage(actor,u,db)
    u.password_hash=hash_password(payload.password)
    u.failed_login_attempts=0
    u.locked_until=None
    db.add(AuditLog(user_id=actor.id,action='user_password_reset',entity_type='user',entity_id=u.id,after_json=json.dumps({'email':u.email})))
    db.commit(); return {'message':'Password reset successfully.'}

@router.post('/users/{user_id}/mailbox')
def provision_user_mailbox(user_id:int,payload:MailboxCreate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    u=db.get(User,user_id)
    if not u: raise HTTPException(404,'Employee account not found.')
    if not u.is_active: raise HTTPException(400,'Reinstate the employee before creating a mailbox.')
    ep=_employee_profile(db,u.id,create=True)
    if not company_mail_provider_status()['ready']:
        raise HTTPException(400,'Actual mailbox creation is not configured. Configure cPanel UAPI in the private config.env file.')
    domain=_company_email_domain(db)
    if not domain or u.email.rsplit('@',1)[-1].lower()!=domain:
        raise HTTPException(400,'This employee login email does not use the configured company domain. Update the email before provisioning.')
    try:
        result=provision_company_mailbox(u.email,payload.password,payload.quota_mb)
        ep.mailbox_status='provisioned'; ep.mailbox_provider=result['provider']; ep.mailbox_last_error=None
        db.add(AuditLog(user_id=actor.id,action='company_mailbox_provisioned',entity_type='user',entity_id=u.id,after_json=json.dumps({'email':u.email,'provider':'cpanel'})))
        db.commit()
        return {'id':u.id,'email':u.email,'mailbox_status':ep.mailbox_status,'message':'Mailbox created. Give the password to the employee through a secure channel; CODEZONE does not store the mailbox password.'}
    except MailboxProvisionError as exc:
        ep.mailbox_status='failed'; ep.mailbox_provider='cpanel'; ep.mailbox_last_error=str(exc)[:250]
        db.add(AuditLog(user_id=actor.id,action='company_mailbox_provision_failed',entity_type='user',entity_id=u.id,after_json=json.dumps({'email':u.email,'reason':str(exc)[:200]})))
        db.commit()
        raise HTTPException(502,str(exc)) from exc

@router.post('/users/{user_id}/mailbox/password')
def reset_company_mailbox_password(user_id:int,payload:MailboxPasswordUpdate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    u=db.get(User,user_id)
    if not u: raise HTTPException(404,'Employee account not found.')
    if not u.is_active: raise HTTPException(400,'Reinstate the employee in CODEZONE before restoring mailbox access.')
    ep=_employee_profile(db,u.id,create=False)
    if not ep or ep.mailbox_status not in ('provisioned','suspended','access_review_required'):
        raise HTTPException(400,'This employee does not have an existing provisioned mailbox. Create the mailbox first.')
    if not company_mail_provider_status()['ready']:
        raise HTTPException(400,'cPanel UAPI is not configured. Restore this mailbox through the email provider or configure cPanel UAPI first.')
    if u.email.rsplit('@',1)[-1].lower()!=_company_email_domain(db):
        raise HTTPException(400,'This mailbox address does not use the configured company domain.')
    try:
        result=change_company_mailbox_password(u.email,payload.password)
        ep.mailbox_status='provisioned';ep.mailbox_provider=result['provider'];ep.mailbox_last_error=None
        db.add(AuditLog(user_id=actor.id,action='company_mailbox_password_reset',entity_type='user',entity_id=u.id,after_json=json.dumps({'email':u.email,'provider':'cpanel'})))
        db.commit()
        return {'id':u.id,'email':u.email,'mailbox_status':ep.mailbox_status,'message':'Mailbox password reset. CODEZONE did not store the password; share it with the employee securely.'}
    except MailboxProvisionError as exc:
        ep.mailbox_status='access_review_required';ep.mailbox_provider='cpanel';ep.mailbox_last_error=str(exc)[:250]
        db.add(AuditLog(user_id=actor.id,action='company_mailbox_password_reset_failed',entity_type='user',entity_id=u.id,after_json=json.dumps({'email':u.email,'reason':str(exc)[:200]})))
        db.commit()
        raise HTTPException(502,str(exc)) from exc

@router.put('/users/{user_id}/status')
def set_user_status(user_id:int, active:bool, db:Session=Depends(get_db), actor:User=Depends(require_roles('owner','admin'))):
    u=db.get(User,user_id)
    if not u: raise HTTPException(404,'User not found.')
    target_role=db.get(Role,u.role_id) if u.role_id else None
    actor_role=db.get(Role,actor.role_id) if actor.role_id else None
    if u.id==actor.id and not active:
        raise HTTPException(400,'You cannot disable your own account.')
    if target_role and target_role.name=='owner' and (not actor_role or actor_role.name!='owner'):
        raise HTTPException(403,'Only the owner can disable an owner.')
    if target_role and target_role.name=='owner' and not active:
        owners=db.query(User).join(Role,Role.id==User.role_id).filter(Role.name=='owner',User.is_active==True).count()
        if owners<=1: raise HTTPException(400,'The last active owner cannot be disabled.')
    before={'is_active':u.is_active}
    u.is_active=active
    ep=_employee_profile(db,u.id,create=False)
    if ep:
        ep.is_active=active
        ep.full_name=ep.full_name or u.name
        ep.company_email=ep.company_email or u.email
        ep.termination_date=None if active else date.today()
        if not active and ep.mailbox_status=='provisioned':
            # Rotate to an unknown, random password: the mailbox and messages remain in place,
            # but the employee can no longer sign in with the password they previously knew.
            try:
                change_company_mailbox_password(u.email,secrets.token_urlsafe(36))
                ep.mailbox_status='suspended'
                ep.mailbox_provider='cpanel'
                ep.mailbox_last_error='Mailbox password rotated during offboarding. Mail and mailbox data are retained; restore access explicitly when appropriate.'
                db.add(AuditLog(user_id=actor.id,action='company_mailbox_suspended',entity_type='user',entity_id=u.id,after_json=json.dumps({'email':u.email,'method':'password_rotation','mailbox_data_retained':True})))
            except MailboxProvisionError as exc:
                ep.mailbox_status='access_review_required'
                ep.mailbox_last_error=('CODEZONE login is disabled, but cPanel mailbox access could not be secured automatically: '+str(exc))[:250]
    db.add(AuditLog(user_id=actor.id,action='employee_reinstated' if active else 'employee_offboarded',entity_type='user',entity_id=u.id,
                    before_json=json.dumps(before),after_json=json.dumps({'is_active':active,'termination_date':ep.termination_date.isoformat() if ep and ep.termination_date else None})))
    db.commit(); return {'id':u.id,'is_active':u.is_active,'termination_date':ep.termination_date.isoformat() if ep and ep.termination_date else None,
                         'mailbox_status':ep.mailbox_status if ep else 'not_configured',
                         'message':'Account enabled. If the mailbox was suspended, reset its mailbox password separately to restore email access.' if active else ('CODEZONE login disabled, but mailbox security needs manual action in the provider.' if ep and ep.mailbox_status=='access_review_required' else ('Employee offboarded. Mailbox password was rotated and mail data retained.' if ep and ep.mailbox_status=='suspended' else 'Employee offboarded; business history was preserved.'))}

def _employee_dict(db: Session, ep: EmployeeProfile):
    linked=db.get(User,ep.user_id) if ep.user_id else None
    role=db.get(Role,linked.role_id) if linked and linked.role_id else None
    return {
        'id':ep.id,'full_name':ep.full_name or (linked.name if linked else 'Unnamed employee'),
        'employee_number':ep.employee_number,'company_email':ep.company_email,
        'phone':ep.phone,'department':ep.department,'job_title':ep.job_title,
        'employment_type':ep.employment_type,'hire_date':ep.hire_date.isoformat() if ep.hire_date else None,
        'termination_date':ep.termination_date.isoformat() if ep.termination_date else None,
        'personal_email':ep.personal_email,'notes':ep.notes,'is_active':bool(ep.is_active),
        'mailbox_status':ep.mailbox_status,'mailbox_provider':ep.mailbox_provider,
        'mailbox_last_error':ep.mailbox_last_error,'created_at':ep.created_at.isoformat() if ep.created_at else None,
        'login_enabled':bool(linked and linked.is_active),'user_id':linked.id if linked else None,
        'login_email':linked.email if linked else None,'login_role':role.name if role else None,
        'last_login_at':linked.last_login_at.isoformat() if linked and linked.last_login_at else None,
    }


def _suggest_employee_email(db: Session, name: str, exclude_profile_id: int | None = None):
    domain=_company_email_domain(db)
    if not domain or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain):
        raise HTTPException(400,'Configure a valid company email domain first, or register this employee without a company email.')
    local=_normalise_local_part(name)
    candidate=f'{local}@{domain}'; suffix=2
    while (db.query(EmployeeProfile).filter(func.lower(EmployeeProfile.company_email)==candidate.lower(),
                                             *([EmployeeProfile.id!=exclude_profile_id] if exclude_profile_id else [])).first()
           or db.query(User).filter(func.lower(User.email)==candidate.lower()).first()):
        candidate=f'{local}{suffix}@{domain}'; suffix+=1
        if suffix>9999: raise HTTPException(409,'Unable to generate a unique company email address.')
    return candidate


@router.get('/employees')
def list_employees(db: Session=Depends(get_db), _: User=Depends(require_roles('owner','admin'))):
    rows=db.query(EmployeeProfile).order_by(EmployeeProfile.is_active.desc(),EmployeeProfile.full_name,EmployeeProfile.id).all()
    mail=company_mail_provider_status()
    return {'items':[_employee_dict(db,x) for x in rows],
            'email_settings':{'domain':_company_email_domain(db),'provider':mail['provider'],'provider_ready':mail['ready'],'provider_label':mail['label']}}


@router.get('/employees/suggest-email')
def suggest_employee_email(name: str, db: Session=Depends(get_db), _: User=Depends(require_roles('owner','admin'))):
    if len(name.strip())<2: raise HTTPException(400,'Enter the employee name first.')
    return {'email':_suggest_employee_email(db,name)}


@router.post('/employees')
def create_employee(payload: EmployeeCreate, db: Session=Depends(get_db), actor: User=Depends(require_roles('owner','admin'))):
    actor_role=_role(db,actor)
    full_name=payload.full_name.strip()
    company_email=_suggest_employee_email(db,full_name) if payload.generate_company_email else (str(payload.company_email).strip().lower() if payload.company_email else None)
    if company_email:
        if db.query(EmployeeProfile).filter(func.lower(EmployeeProfile.company_email)==company_email.lower()).first():
            raise HTTPException(409,'That company email is already assigned to an employee.')
        # Existing user/login email can only be reused when linking to that account, never by accident.
        if db.query(User).filter(func.lower(User.email)==company_email.lower()).first():
            raise HTTPException(409,'That email is already a CODEZONE login. Open the employee profile linked to that user instead.')
        domain=_company_email_domain(db)
        if domain and company_email.rsplit('@',1)[-1].lower()!=domain:
            raise HTTPException(400,'Company email must use the configured company domain. Change the domain or use no company email for this employee.')
    elif payload.generate_company_email:
        raise HTTPException(400,'A company domain is required to generate a company email.')

    login_user=None
    if payload.create_login:
        login_email=str(payload.login_email or company_email or '').strip().lower()
        if not login_email: raise HTTPException(400,'Provide a login email or generate a company email first.')
        if db.query(User).filter(func.lower(User.email)==login_email).first():
            raise HTTPException(409,'That login email is already used by another CODEZONE account.')
        role=db.query(Role).filter(Role.name==payload.login_role).first()
        if not role: raise HTTPException(400,'Choose a valid CODEZONE role.')
        if role.name in ('owner','admin') and (not actor_role or actor_role.name!='owner'):
            raise HTTPException(403,'Only the owner can create owner/admin accounts.')
        login_user=User(name=full_name,email=login_email,password_hash=hash_password(payload.login_password or ''),role_id=role.id,is_active=True)
        db.add(login_user); db.flush()

    number=(payload.employee_number or '').strip() or None
    if number and db.query(EmployeeProfile).filter(func.lower(EmployeeProfile.employee_number)==number.lower()).first():
        db.rollback(); raise HTTPException(409,'Employee number already exists.')
    ep=EmployeeProfile(user_id=login_user.id if login_user else None,full_name=full_name,company_email=company_email,
        is_active=True,employee_number=number,phone=(payload.phone or '').strip() or None,
        department=(payload.department or '').strip() or None,job_title=(payload.job_title or '').strip() or None,
        employment_type=payload.employment_type,hire_date=payload.hire_date,
        personal_email=str(payload.personal_email).strip().lower() if payload.personal_email else None,
        notes=(payload.notes or '').strip() or None,mailbox_status='address_only' if company_email else 'not_configured')
    db.add(ep)
    try:
        db.flush()
        if not ep.employee_number: ep.employee_number=_suggest_employee_number(db,ep.id)
        db.add(AuditLog(user_id=actor.id,action='employee_profile_created',entity_type='employee_profile',entity_id=ep.id,
            after_json=json.dumps({'full_name':full_name,'employee_number':ep.employee_number,'company_email':company_email,'login_created':bool(login_user)})))
        db.commit(); db.refresh(ep)
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409,'Employee number or company email already exists.') from exc
    db.refresh(ep)
    return {'employee':_employee_dict(db,ep),'message':('Employee registered with a separate CODEZONE login account.' if login_user else 'Employee profile registered. Login access and mailbox can be added separately.')}


@router.put('/employees/{employee_id}')
def update_employee(employee_id:int,payload:EmployeeUpdate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    ep=db.get(EmployeeProfile,employee_id)
    if not ep: raise HTTPException(404,'Employee record not found.')
    data=payload.model_dump(exclude_unset=True)
    before=_employee_dict(db,ep)
    if 'full_name' in data and data['full_name'] is not None:
        ep.full_name=data['full_name'].strip()
        linked_user=db.get(User,ep.user_id) if ep.user_id else None
        if linked_user: linked_user.name=ep.full_name
    if 'employee_number' in data and data['employee_number'] is not None:
        number=data['employee_number'].strip() or None
        duplicate=db.query(EmployeeProfile).filter(func.lower(EmployeeProfile.employee_number)==(number or '').lower(),EmployeeProfile.id!=ep.id).first() if number else None
        if duplicate: raise HTTPException(409,'Employee number already exists.')
        ep.employee_number=number
    if 'company_email' in data:
        value=str(data['company_email']).strip().lower() if data['company_email'] else None
        if ep.mailbox_status in ('provisioned','suspended','access_review_required') and value!=(ep.company_email or '').lower():
            raise HTTPException(400,'This employee has a mailbox or mailbox security state. Secure or migrate it with the email provider before changing its address.')
        if value:
            if db.query(EmployeeProfile).filter(func.lower(EmployeeProfile.company_email)==value,EmployeeProfile.id!=ep.id).first():
                raise HTTPException(409,'That company email is already assigned to another employee.')
            if db.query(User).filter(func.lower(User.email)==value,User.id!=(ep.user_id or -1)).first():
                raise HTTPException(409,'That email is already in use as a CODEZONE login.')
            domain=_company_email_domain(db)
            if domain and value.rsplit('@',1)[-1].lower()!=domain: raise HTTPException(400,'Company email must match the configured company domain.')
        ep.company_email=value
        ep.mailbox_status='address_only' if value else 'not_configured'
    for field in ('phone','department','job_title','employment_type','hire_date','notes'):
        if field in data and data[field] is not None:
            value=data[field]
            if field in ('phone','department','job_title','notes'): value=value.strip() or None
            setattr(ep,field,value)
    if 'personal_email' in data and data['personal_email'] is not None:
        ep.personal_email=str(data['personal_email']).strip().lower()
    try:
        db.add(AuditLog(user_id=actor.id,action='employee_profile_updated',entity_type='employee_profile',entity_id=ep.id,
            before_json=json.dumps(before,default=str),after_json=json.dumps(_employee_dict(db,ep),default=str)))
        db.commit();db.refresh(ep)
    except IntegrityError as exc:
        db.rollback();raise HTTPException(409,'Employee number or company email already exists.') from exc
    return {'employee':_employee_dict(db,ep),'message':'Employee details updated.'}


@router.put('/employees/{employee_id}/status')
def set_employee_status(employee_id:int,active:bool,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    ep=db.get(EmployeeProfile,employee_id)
    if not ep: raise HTTPException(404,'Employee record not found.')
    linked=db.get(User,ep.user_id) if ep.user_id else None
    if linked and linked.id==actor.id and not active: raise HTTPException(400,'You cannot disable your own linked login by offboarding this employee.')
    if linked and _role(db,linked) and _role(db,linked).name=='owner' and _role(db,actor) and _role(db,actor).name!='owner':
        raise HTTPException(403,'Only the owner can offboard an owner.')
    if linked and _role(db,linked) and _role(db,linked).name=='owner' and not active:
        active_owners=db.query(User).join(Role,Role.id==User.role_id).filter(Role.name=='owner',User.is_active==True).count()
        if active_owners<=1: raise HTTPException(400,'The last active owner cannot be offboarded.')
    ep.is_active=active;ep.termination_date=None if active else date.today()
    mailbox_warning=None
    if linked: linked.is_active=active
    if not active and ep.mailbox_status=='provisioned' and ep.company_email:
        try:
            change_company_mailbox_password(ep.company_email,secrets.token_urlsafe(36))
            ep.mailbox_status='suspended';ep.mailbox_provider='cpanel'
            ep.mailbox_last_error='Mailbox access was suspended during employee offboarding. Mail data are retained.'
        except MailboxProvisionError as exc:
            ep.mailbox_status='access_review_required';ep.mailbox_last_error=('Employee CODEZONE access was disabled but mailbox security requires manual action: '+str(exc))[:250]
            mailbox_warning=ep.mailbox_last_error
    db.add(AuditLog(user_id=actor.id,action='employee_offboarded' if not active else 'employee_reinstated',entity_type='employee_profile',entity_id=ep.id,
        after_json=json.dumps({'active':active,'linked_user_id':ep.user_id,'company_email':ep.company_email,'mailbox_status':ep.mailbox_status})))
    db.commit();db.refresh(ep)
    return {'employee':_employee_dict(db,ep),'message':mailbox_warning or ('Employee reinstated. Any suspended mailbox must be explicitly restored.' if active else 'Employee offboarded. CODEZONE login is disabled where linked; operational history is retained.')}


@router.post('/employees/{employee_id}/mailbox')
def provision_employee_mailbox(employee_id:int,payload:MailboxCreate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    ep=db.get(EmployeeProfile,employee_id)
    if not ep: raise HTTPException(404,'Employee record not found.')
    if not ep.is_active: raise HTTPException(400,'Reinstate the employee before creating a company mailbox.')
    email=(ep.company_email or '').strip().lower()
    if not email: raise HTTPException(400,'Assign or generate a company email address before creating its mailbox.')
    domain=_company_email_domain(db)
    if not domain or email.rsplit('@',1)[-1]!=domain: raise HTTPException(400,'Employee email must match the configured company domain.')
    if not company_mail_provider_status()['ready']: raise HTTPException(400,'A real company email provider is not configured. The address exists only inside CODEZONE.')
    try:
        result=provision_company_mailbox(email,payload.password,payload.quota_mb)
        ep.mailbox_status='provisioned';ep.mailbox_provider=result['provider'];ep.mailbox_last_error=None
        db.add(AuditLog(user_id=actor.id,action='company_mailbox_provisioned',entity_type='employee_profile',entity_id=ep.id,after_json=json.dumps({'email':email,'provider':result['provider']})))
        db.commit();db.refresh(ep)
        return {'employee':_employee_dict(db,ep),'message':'Real mailbox created successfully. Share the mailbox password through a separate secure channel.'}
    except MailboxProvisionError as exc:
        ep.mailbox_status='failed';ep.mailbox_provider='cpanel';ep.mailbox_last_error=str(exc)[:250]
        db.add(AuditLog(user_id=actor.id,action='company_mailbox_provision_failed',entity_type='employee_profile',entity_id=ep.id,after_json=json.dumps({'email':email,'reason':str(exc)[:200]})))
        db.commit();raise HTTPException(502,str(exc)) from exc


@router.post('/employees/{employee_id}/mailbox/password')
def reset_employee_mailbox_password(employee_id:int,payload:MailboxPasswordUpdate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    ep=db.get(EmployeeProfile,employee_id)
    if not ep: raise HTTPException(404,'Employee record not found.')
    if not ep.is_active: raise HTTPException(400,'Reinstate the employee before restoring mailbox access.')
    if not ep.company_email or ep.mailbox_status not in ('provisioned','suspended','access_review_required'):
        raise HTTPException(400,'This employee does not have an existing provisioned mailbox.')
    if not company_mail_provider_status()['ready']: raise HTTPException(400,'Configure the email provider before restoring this mailbox.')
    if ep.company_email.rsplit('@',1)[-1].lower()!=_company_email_domain(db): raise HTTPException(400,'Mailbox address does not use the configured company domain.')
    try:
        result=change_company_mailbox_password(ep.company_email,payload.password)
        ep.mailbox_status='provisioned';ep.mailbox_provider=result['provider'];ep.mailbox_last_error=None
        db.add(AuditLog(user_id=actor.id,action='company_mailbox_password_reset',entity_type='employee_profile',entity_id=ep.id,after_json=json.dumps({'email':ep.company_email,'provider':result['provider']})))
        db.commit();db.refresh(ep)
        return {'employee':_employee_dict(db,ep),'message':'Mailbox password changed; CODEZONE does not store the mailbox password.'}
    except MailboxProvisionError as exc:
        ep.mailbox_status='access_review_required';ep.mailbox_last_error=str(exc)[:250]
        db.add(AuditLog(user_id=actor.id,action='company_mailbox_password_reset_failed',entity_type='employee_profile',entity_id=ep.id,after_json=json.dumps({'email':ep.company_email,'reason':str(exc)[:200]})))
        db.commit();raise HTTPException(502,str(exc)) from exc


@router.put('/users/{user_id}/access')
def set_login_access(user_id:int,active:bool,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin'))):
    target=db.get(User,user_id)
    if not target: raise HTTPException(404,'User account not found.')
    actor_role=_role(db,actor);target_role=_role(db,target)
    if target.id==actor.id and not active: raise HTTPException(400,'You cannot disable your own login.')
    if target_role and target_role.name=='owner' and (not actor_role or actor_role.name!='owner'):
        raise HTTPException(403,'Only the owner can change another owner account.')
    if target_role and target_role.name=='owner' and not active:
        count=db.query(User).join(Role,Role.id==User.role_id).filter(Role.name=='owner',User.is_active==True).count()
        if count<=1: raise HTTPException(400,'The last active owner account cannot be disabled.')
    before=target.is_active;target.is_active=active
    db.add(AuditLog(user_id=actor.id,action='login_access_enabled' if active else 'login_access_disabled',entity_type='user',entity_id=target.id,
        before_json=json.dumps({'is_active':before}),after_json=json.dumps({'is_active':active})))
    db.commit()
    return {'id':target.id,'is_active':target.is_active,'message':'Login access enabled.' if active else 'Login access disabled. Employee profile and any mailbox are unchanged.'}


@router.get('/products')
def products(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [{'id':p.id,'sku':p.sku,'name':p.name,'item_type':getattr(p,'item_type','product'),'barcode':getattr(p,'barcode',None),'brand':getattr(p,'brand',None),'track_inventory':getattr(p,'track_inventory',True),'buying_price':str(p.buying_price),'selling_price':str(p.selling_price),'stock':str(p.current_stock),'minimum_stock':str(p.minimum_stock),'reorder_level':str(p.reorder_level)} for p in db.query(Product).filter(Product.is_active==True).order_by(Product.name).all()]

@router.delete('/products/{product_id}')
def delete_product(product_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles('owner','admin','manager','storekeeper'))):
    product = db.get(Product, product_id)

    if not product:
        raise HTTPException(404, 'Product not found.')

    product.is_active = False
    db.add(AuditLog(user_id=_.id,action='product_deleted',entity_type='product',entity_id=product.id,
                    after_json=str({'is_active':False})))
    db.commit()
    return {'message': 'Product deleted successfully.'}

@router.put('/products/{product_id}')
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db), actor: User = Depends(require_roles('owner','admin','manager'))):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(404, 'Product not found.')

    before={'sku':product.sku,'name':product.name,'item_type':getattr(product,'item_type','product'),'barcode':getattr(product,'barcode',None),'brand':getattr(product,'brand',None),'track_inventory':getattr(product,'track_inventory',True),'buying_price':str(product.buying_price),
            'selling_price':str(product.selling_price),'minimum_stock':str(product.minimum_stock),
            'reorder_level':str(product.reorder_level)}
    try:
        data=payload.model_dump(exclude_unset=True)
        if 'sku' in data:
            sku=str(data['sku']).strip()
            if not sku: raise HTTPException(400,'SKU is required.')
            duplicate=db.query(Product).filter(Product.sku==sku,Product.id!=product.id).first()
            if duplicate: raise HTTPException(409,'SKU already exists.')
            product.sku=sku
        if 'name' in data:
            name=str(data['name']).strip()
            if not name: raise HTTPException(400,'Product name is required.')
            product.name=name
        if 'buying_price' in data and Decimal(str(data['buying_price'])) != Decimal(product.buying_price or 0) and Decimal(product.current_stock or 0) > 0:
            raise HTTPException(409,'Buying price cannot be edited while stock exists. Use a stock/cost adjustment workflow so inventory valuation remains auditable.')
        if 'item_type' in data:
            if data['item_type'] not in ('product','service'):
                raise HTTPException(400,'Item type must be product or service.')
            product.item_type=data['item_type']
            if product.item_type=='service':
                product.track_inventory=False
        if 'barcode' in data:
            barcode=(str(data['barcode']).strip() if data['barcode'] else None)
            duplicate=db.query(Product).filter(Product.barcode==barcode,Product.id!=product.id).first() if barcode else None
            if duplicate: raise HTTPException(409,'Barcode already exists.')
            product.barcode=barcode
        if 'brand' in data:
            product.brand=(str(data['brand']).strip() if data['brand'] else None)
        if 'track_inventory' in data and product.item_type!='service':
            product.track_inventory=bool(data['track_inventory'])
        for field in ('buying_price','selling_price','minimum_stock','reorder_level'):
            if field in data:
                value=Decimal(str(data[field]))
                if value<0: raise HTTPException(400,f'{field.replace("_"," ").title()} cannot be negative.')
                setattr(product,field,value)
        if Decimal(product.selling_price) <= 0: raise HTTPException(400,'Selling price must be greater than zero.')
        if Decimal(product.selling_price) < Decimal(product.buying_price): raise HTTPException(400,'Selling price cannot be below buying price.')
        db.add(AuditLog(user_id=actor.id,action='product_updated',entity_type='product',entity_id=product.id,
                        before_json=str(before),after_json=str({'sku':product.sku,'name':product.name,'item_type':product.item_type,'barcode':product.barcode,'brand':product.brand,'track_inventory':product.track_inventory,'buying_price':str(product.buying_price),'selling_price':str(product.selling_price),'minimum_stock':str(product.minimum_stock),'reorder_level':str(product.reorder_level)})))
        db.commit()
        db.refresh(product)
    except HTTPException:
        db.rollback(); raise
    except IntegrityError:
        db.rollback(); raise HTTPException(409,'Product SKU already exists.')
    except (TypeError, ValueError, ArithmeticError) as exc:
        db.rollback(); raise HTTPException(400,f'Invalid product data: {exc}')
    return {'message':'Product updated successfully.','id':product.id,'sku':product.sku,'name':product.name,'item_type':product.item_type,'barcode':product.barcode,'brand':product.brand,'track_inventory':product.track_inventory,
            'buying_price':str(product.buying_price),'selling_price':str(product.selling_price),
            'stock':str(product.current_stock),'minimum_stock':str(product.minimum_stock),'reorder_level':str(product.reorder_level)}


@router.get('/customers')
def customers(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [{'id':c.id,'name':c.name,'phone':c.phone,'address':c.address,'credit_limit':str(c.credit_limit),'opening_balance':str(c.opening_balance),'payment_terms_days':c.payment_terms_days,'group_id':getattr(c,'group_id',None)} for c in db.query(Customer).filter(Customer.is_active==True).order_by(Customer.name).all()]

@router.post('/customers')
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db), actor: User = Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    actor_role=db.get(Role,actor.role_id) if actor.role_id else None
    credit_limit=payload.credit_limit
    if credit_limit<0: raise HTTPException(400,'Credit limit cannot be negative.')
    if actor_role and actor_role.name in ('salesperson','cashier') and credit_limit>0:
        raise HTTPException(403,'Only management/accounting roles can assign a customer credit limit.')
    c=Customer(name=payload.name.strip(), phone=payload.phone, address=payload.address, credit_limit=credit_limit, payment_terms_days=payload.payment_terms_days)
    if not c.name: raise HTTPException(400,'Customer name is required.')
    db.add(c); db.flush(); db.add(AuditLog(user_id=actor.id,action='customer_created',entity_type='customer',entity_id=c.id,after_json=json.dumps({'name':c.name,'credit_limit':str(c.credit_limit)}))); db.commit(); db.refresh(c); return {'id':c.id,'name':c.name}

@router.put('/customers/{customer_id}')
def update_customer(customer_id:int,payload:CustomerCreate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin','manager','salesperson','cashier','accountant'))):
    c=db.get(Customer,customer_id)
    if not c: raise HTTPException(404,'Customer not found.')
    role=db.get(Role,actor.role_id) if actor.role_id else None
    privileged_credit_roles=('owner','admin','manager','accountant')
    if role and role.name not in privileged_credit_roles and payload.credit_limit != Decimal(c.credit_limit or 0):
        raise HTTPException(403,'Only management/accounting can change a customer credit limit.')
    before={'name':c.name,'phone':c.phone,'address':c.address,'credit_limit':str(c.credit_limit),'opening_balance':str(c.opening_balance),'payment_terms_days':c.payment_terms_days}
    c.name=payload.name.strip(); c.phone=payload.phone; c.address=payload.address; c.credit_limit=payload.credit_limit; c.payment_terms_days=payload.payment_terms_days
    db.add(AuditLog(user_id=actor.id,action='customer_updated',entity_type='customer',entity_id=c.id,before_json=json.dumps(before),after_json=json.dumps({'name':c.name,'phone':c.phone,'address':c.address,'credit_limit':str(c.credit_limit),'opening_balance':str(c.opening_balance),'payment_terms_days':c.payment_terms_days}))); db.commit(); db.refresh(c)
    return {'id':c.id,'name':c.name}

@router.get('/suppliers')
def suppliers(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [{'id':s.id,'name':s.name,'phone':s.phone,'address':s.address,'opening_balance':str(s.opening_balance),'payment_terms_days':s.payment_terms_days} for s in db.query(Supplier).filter(Supplier.is_active==True).order_by(Supplier.name).all()]

@router.post('/suppliers')
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db), _: User = Depends(require_roles('owner','admin','manager','storekeeper'))):
    s=Supplier(name=payload.name.strip(), phone=payload.phone, address=payload.address, payment_terms_days=payload.payment_terms_days)
    if not s.name: raise HTTPException(400,'Supplier name is required.')
    db.add(s); db.flush(); db.add(AuditLog(user_id=_.id,action='supplier_created',entity_type='supplier',entity_id=s.id,after_json=json.dumps({'name':s.name}))); db.commit(); db.refresh(s); return {'id':s.id,'name':s.name}

@router.put('/suppliers/{supplier_id}')
def update_supplier(supplier_id:int,payload:SupplierCreate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin','manager','storekeeper','accountant'))):
    s=db.get(Supplier,supplier_id)
    if not s: raise HTTPException(404,'Supplier not found.')
    before={'name':s.name,'phone':s.phone,'address':s.address,'payment_terms_days':s.payment_terms_days}
    s.name=payload.name.strip(); s.phone=payload.phone; s.address=payload.address; s.payment_terms_days=payload.payment_terms_days
    db.add(AuditLog(user_id=actor.id,action='supplier_updated',entity_type='supplier',entity_id=s.id,before_json=json.dumps(before),after_json=json.dumps({'name':s.name,'phone':s.phone,'address':s.address,'payment_terms_days':s.payment_terms_days}))); db.commit(); db.refresh(s)
    return {'id':s.id,'name':s.name}

@router.get('/banks')
def banks(db: Session = Depends(get_db), user: User = Depends(current_user)):
    role=db.get(Role,user.role_id) if user.role_id else None
    privileged=role and role.name in ('owner','admin','accountant')
    out=[]
    for b in db.query(BankAccount).order_by(BankAccount.bank_name).all():
        number=b.account_number or ''
        out.append({'id':b.id,'bank_name':b.bank_name,'account_name':b.account_name,
                    'account_number':number if privileged else ('*'*max(0,len(number)-4)+number[-4:]),
                    'opening_balance':str(b.opening_balance)})
    return out

@router.put('/banks/{bank_id}')
def update_bank(bank_id:int,payload:BankCreate,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin','accountant'))):
    b=db.get(BankAccount,bank_id)
    if not b: raise HTTPException(404,'Bank account not found.')
    other=db.query(BankAccount).filter(BankAccount.account_number==payload.account_number.strip(),BankAccount.id!=b.id).first()
    if other: raise HTTPException(409,'Account number already exists.')
    before={'bank_name':b.bank_name,'account_name':b.account_name,'account_number_last4':b.account_number[-4:],'opening_balance':str(b.opening_balance)}
    b.bank_name=payload.bank_name.strip(); b.account_name=payload.account_name.strip(); b.account_number=payload.account_number.strip(); b.opening_balance=payload.opening_balance
    db.add(AuditLog(user_id=actor.id,action='bank_updated',entity_type='bank_account',entity_id=b.id,before_json=json.dumps(before),after_json=json.dumps({'bank_name':b.bank_name,'account_name':b.account_name,'account_number_last4':b.account_number[-4:],'opening_balance':str(b.opening_balance)}))); db.commit(); db.refresh(b)
    return {'id':b.id,'bank_name':b.bank_name,'account_number':b.account_number}

@router.post('/banks')
def create_bank(payload: BankCreate, db: Session = Depends(get_db), _: User = Depends(require_roles('owner','admin','accountant'))):
    b=BankAccount(bank_name=payload.bank_name.strip(), account_name=payload.account_name.strip(), account_number=payload.account_number.strip(), opening_balance=payload.opening_balance)
    if not b.bank_name or not b.account_name or not b.account_number: raise HTTPException(400,'Bank, account name and account number are required.')
    db.add(b); db.flush(); db.add(AuditLog(user_id=_.id,action='bank_created',entity_type='bank_account',entity_id=b.id,after_json=json.dumps({'bank_name':b.bank_name,'account_name':b.account_name,'account_number_last4':b.account_number[-4:]}))); db.commit(); db.refresh(b); return {'id':b.id,'bank_name':b.bank_name,'account_number':b.account_number}


@router.get('/units')
def list_units(db:Session=Depends(get_db), _:User=Depends(current_user)):
    return [{'id':u.id,'name':u.name,'symbol':u.symbol} for u in db.query(Unit).filter(Unit.is_active if hasattr(Unit,'is_active') else True).order_by(Unit.name).all()]

@router.post('/units')
def create_unit(payload:UnitCreatePayload, db:Session=Depends(get_db), _:User=Depends(require_roles('owner','admin','manager','storekeeper'))):
    name=payload.name.strip(); symbol=payload.symbol.strip()
    if not name or not symbol: raise HTTPException(400,'Unit name and symbol are required.')
    if db.query(Unit).filter((Unit.name==name)|(Unit.symbol==symbol)).first(): raise HTTPException(409,'Unit already exists.')
    u=Unit(name=name,symbol=symbol); db.add(u); db.flush(); db.add(AuditLog(user_id=_.id,action='unit_created',entity_type='unit',entity_id=u.id,after_json=json.dumps({'name':u.name,'symbol':u.symbol}))); db.commit(); db.refresh(u); return {'id':u.id,'name':u.name,'symbol':u.symbol}

@router.get('/products/{product_id}/conversions')
def product_conversions(product_id:int, db:Session=Depends(get_db), _:User=Depends(current_user)):
    rows=db.query(ProductUnitConversion).filter(ProductUnitConversion.product_id==product_id).all()
    return [{'id':r.id,'from_unit_id':r.from_unit_id,'to_unit_id':r.to_unit_id,'factor':str(r.factor)} for r in rows]

@router.post('/products/{product_id}/conversions')
def add_product_conversion(product_id:int,payload:ConversionCreate,db:Session=Depends(get_db),_:User=Depends(require_roles('owner','admin','manager','storekeeper'))):
    if not db.get(Product,product_id): raise HTTPException(404,'Product not found.')
    factor=payload.factor
    if factor<=0: raise HTTPException(400,'Conversion factor must be greater than zero.')
    r=ProductUnitConversion(product_id=product_id,from_unit_id=payload.from_unit_id,to_unit_id=payload.to_unit_id,factor=factor); db.add(r); db.commit(); db.refresh(r); return {'id':r.id,'factor':str(r.factor)}

@router.get('/empty-containers')
def empty_containers(db:Session=Depends(get_db),_:User=Depends(current_user)):
    return [{'id':x.id,'name':x.name,'symbol':x.symbol,'deposit_value':str(x.deposit_value),'current_stock':str(x.current_stock)} for x in db.query(EmptyContainerType).filter(EmptyContainerType.is_active==True).order_by(EmptyContainerType.name).all()]

@router.post('/empty-containers')
def create_empty_container(payload:ContainerCreate,db:Session=Depends(get_db),_:User=Depends(require_roles('owner','admin','manager','storekeeper'))):
    name=payload.name.strip(); symbol=payload.symbol.strip()
    if not name or not symbol: raise HTTPException(400,'Container name and symbol are required.')
    x=EmptyContainerType(name=name,symbol=symbol,deposit_value=payload.deposit_value,current_stock=payload.current_stock); db.add(x); db.flush()
    if x.current_stock: db.add(EmptyContainerMovement(container_type_id=x.id,movement_type='opening',quantity=x.current_stock,reference_type='setup'))
    db.add(AuditLog(user_id=_.id,action='container_created',entity_type='empty_container_type',entity_id=x.id,after_json=json.dumps({'name':x.name,'symbol':x.symbol,'deposit_value':str(x.deposit_value),'current_stock':str(x.current_stock)}))); db.commit(); db.refresh(x); return {'id':x.id,'name':x.name,'symbol':x.symbol,'deposit_value':str(x.deposit_value),'current_stock':str(x.current_stock)}

@router.post('/empty-containers/{container_id}/adjust')
def adjust_empty_container(container_id:int,payload:dict,db:Session=Depends(get_db),_:User=Depends(require_roles('owner','admin','manager','storekeeper'))):
    x=db.get(EmptyContainerType,container_id)
    if not x: raise HTTPException(404,'Container type not found.')
    qty=payload.get('quantity',0); movement=str(payload.get('movement_type','adjustment'))
    if not qty or float(qty)==0: raise HTTPException(400,'Quantity cannot be zero.')
    x.current_stock += qty; db.add(EmptyContainerMovement(container_type_id=x.id,movement_type=movement,quantity=qty,reference_type='manual')); db.commit(); return {'id':x.id,'current_stock':str(x.current_stock)}


class StockAdjustmentPayload(BaseModel):
    quantity: Decimal
    reason: str = Field(min_length=3,max_length=255)

@router.post('/products/{product_id}/stock-adjust')
def adjust_product_stock(product_id:int,payload:StockAdjustmentPayload,db:Session=Depends(get_db),actor:User=Depends(require_roles('owner','admin','manager','storekeeper'))):
    p=db.execute(select(Product).where(Product.id==product_id).with_for_update()).scalar_one_or_none()
    if not p or not p.is_active: raise HTTPException(404,'Active product not found.')
    if payload.quantity==0: raise HTTPException(400,'Adjustment quantity cannot be zero.')
    new_stock=Decimal(p.current_stock)+Decimal(payload.quantity)
    if new_stock<0: raise HTTPException(400,'Stock adjustment cannot make stock negative.')
    before=str(p.current_stock); p.current_stock=new_stock
    movement=StockMovement(product_id=p.id,movement_type='stock_adjustment',quantity=payload.quantity,reference_type='manual',reference_id=p.id)
    db.add(movement); db.flush()
    adj=account(db,'6100'); inv=account(db,'1200')
    if payload.quantity > 0:
        post_journal(db,'Stock adjustment gain','stock_adjustment',movement.id,[{'account_id':inv.id,'debit':payload.quantity*p.buying_price,'credit':0},{'account_id':adj.id,'debit':0,'credit':payload.quantity*p.buying_price}])
    else:
        post_journal(db,'Stock adjustment loss','stock_adjustment',movement.id,[{'account_id':adj.id,'debit':(-payload.quantity)*p.buying_price,'credit':0},{'account_id':inv.id,'debit':0,'credit':(-payload.quantity)*p.buying_price}])
    db.add(AuditLog(user_id=actor.id,action='stock_adjusted',entity_type='product',entity_id=p.id,before_json=json.dumps({'stock':before}),after_json=json.dumps({'stock':str(new_stock),'adjustment':str(payload.quantity),'reason':payload.reason})))
    db.commit(); return {'id':p.id,'stock':str(p.current_stock)}

@router.post('/sales/{sale_id}/void')
def void_sale(sale_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles('owner','admin','manager'))):
    sale=db.execute(select(Sale).where(Sale.id==sale_id).with_for_update()).scalar_one_or_none()
    if not sale: raise HTTPException(404,'Sale not found.')
    if sale.status!='posted': raise HTTPException(409,'Only posted sales can be voided.')
    if Decimal(sale.paid or 0)>0: raise HTTPException(409,'Paid invoices cannot be voided. Use a return/refund workflow.')
    from app.models.models import SalesReturn
    if db.query(SalesReturn).filter(SalesReturn.sale_id==sale.id,SalesReturn.status=='posted').first(): raise HTTPException(409,'Invoice has returns and cannot be voided.')
    for item in db.query(SaleItem).filter(SaleItem.sale_id==sale.id).all():
        product=db.execute(select(Product).where(Product.id==item.product_id).with_for_update()).scalar_one()
        qty=Decimal(item.quantity)
        if item.unit_id and product.base_unit_id and item.unit_id!=product.base_unit_id:
            conv=db.query(ProductUnitConversion).filter_by(product_id=product.id,from_unit_id=item.unit_id,to_unit_id=product.base_unit_id).first()
            if conv: qty*=Decimal(conv.factor)
        product.current_stock+=qty
        db.add(StockMovement(product_id=product.id,movement_type='sale_void',quantity=qty,reference_type='sale',reference_id=sale.id))
    journals=db.query(JournalEntry).filter(JournalEntry.reference_type=='sale',JournalEntry.reference_id==sale.id,JournalEntry.status=='posted').all()
    for j in journals: j.status='void'
    sale.status='void'
    db.add(AuditLog(user_id=user.id,action='sale_voided',entity_type='sale',entity_id=sale.id,after_json=json.dumps({'invoice_no':sale.invoice_no})))
    db.commit(); return {'status':'void','id':sale.id,'invoice_no':sale.invoice_no}

@router.put('/opening-balances/master')
def set_opening_balance(payload:OpeningBalanceSet,db:Session=Depends(get_db),user:User=Depends(require_roles('owner','admin'))):
    if any((db.query(Sale).count(),db.query(Purchase).count(),db.query(Expense).count(),db.query(CashTransaction).count(),db.query(BankTransaction).count(),db.query(CustomerPayment).count(),db.query(SupplierPayment).count(),db.query(JournalEntry).count())):
        raise HTTPException(409,'Opening balances can only be edited before live transactions are posted.')
    model={'customer':Customer,'supplier':Supplier,'bank':BankAccount}[payload.entity_type]
    obj=db.get(model,payload.entity_id)
    if not obj: raise HTTPException(404,f'{payload.entity_type.title()} not found.')
    before=str(obj.opening_balance or 0); obj.opening_balance=payload.amount
    db.add(AuditLog(user_id=user.id,action='opening_balance_updated',entity_type=payload.entity_type,entity_id=obj.id,before_json=json.dumps({'opening_balance':before}),after_json=json.dumps({'opening_balance':str(obj.opening_balance)})))
    db.commit(); return {'entity_type':payload.entity_type,'entity_id':obj.id,'opening_balance':str(obj.opening_balance)}

@router.post('/opening-balances/apply')
def apply_opening_balances(payload:OpeningBalancesApply, db:Session=Depends(get_db), user:User=Depends(require_roles('owner','admin'))):
    if db.query(AuditLog).filter(AuditLog.action=='opening_balances_applied').first():
        raise HTTPException(409,'Opening balances have already been applied.')
    # Opening balances must be applied before live business transactions. Otherwise
    # current stock and cash/bank figures would be mixed with already-posted activity.
    if any((db.query(Sale).count(), db.query(Purchase).count(), db.query(Expense).count(),
            db.query(CashTransaction).count(), db.query(BankTransaction).count(),
            db.query(CustomerPayment).count(), db.query(SupplierPayment).count(),
            db.query(JournalEntry).count())):
        raise HTTPException(409,'Opening balances must be applied before any live transactions are posted.')
    def acct(code):
        a=db.query(Account).filter(Account.code==code).first()
        if not a: raise HTTPException(500,f'Account {code} is not configured.')
        return a.id
    cash=Decimal(payload.cash_opening_balance)
    ar=sum((Decimal(c.opening_balance or 0) for c in db.query(Customer).all()),Decimal('0'))
    ap=sum((Decimal(s.opening_balance or 0) for s in db.query(Supplier).all()),Decimal('0'))
    inventory=sum((Decimal(p.current_stock or 0)*Decimal(p.buying_price or 0) for p in db.query(Product).filter(Product.is_active==True)),Decimal('0'))
    bank=sum((Decimal(b.opening_balance or 0) for b in db.query(BankAccount).all()),Decimal('0'))
    assets=ar+inventory+bank+cash
    equity=assets-ap
    lines=[]
    if inventory: lines.append({'account_id':acct('1200'),'debit':inventory,'credit':0})
    if ar: lines.append({'account_id':acct('1100'),'debit':ar,'credit':0})
    if cash: lines.append({'account_id':acct('1000'),'debit':cash,'credit':0})
    if bank: lines.append({'account_id':acct('1010'),'debit':bank,'credit':0})
    if equity>=0: lines.append({'account_id':acct('3000'),'debit':0,'credit':equity})
    else: lines.append({'account_id':acct('3000'),'debit':-equity,'credit':0})
    if ap: lines.append({'account_id':acct('2000'),'debit':0,'credit':ap})
    entry=JournalEntry(entry_date=datetime.now(timezone.utc).replace(tzinfo=None),reference_type='opening_balance',description='Opening balances',status='posted'); db.add(entry); db.flush()
    for x in lines: db.add(JournalLine(journal_entry_id=entry.id,account_id=x['account_id'],debit=x['debit'],credit=x['credit']))
    if cash:
        db.add(CashTransaction(transaction_type='opening_balance',amount=cash,reference=f'OPENING-{entry.id}',description='Opening cash balance'))
    db.add(AuditLog(user_id=user.id,action='opening_balances_applied',entity_type='opening_balance',entity_id=entry.id,after_json=str({'inventory':str(inventory),'receivables':str(ar),'cash':str(cash),'bank':str(bank),'payables':str(ap),'owner_equity':str(equity)})))
    db.commit(); return {'entry_id':entry.id,'inventory':str(inventory),'receivables':str(ar),'cash':str(cash),'bank':str(bank),'payables':str(ap),'owner_equity':str(equity)}
