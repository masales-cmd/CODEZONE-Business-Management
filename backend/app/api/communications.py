from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.security import current_user
from app.models.models import (
    User, Role, Branch, Conversation, ConversationParticipant, TeamMessage, TeamTask,
    Notification, AuditLog,
)

router = APIRouter(prefix='/api/communications', tags=['team communication'])

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class ConversationCreate(BaseModel):
    recipient_ids: list[int] = Field(min_length=1, max_length=50)
    subject: str | None = Field(default=None, max_length=180)
    initial_message: str = Field(min_length=1, max_length=10000)
    branch_id: int | None = None
    linked_entity_type: str | None = Field(default=None, max_length=60)
    linked_entity_id: int | None = Field(default=None, ge=1)

    @field_validator('initial_message')
    @classmethod
    def nonblank_message(cls, value):
        if not value.strip(): raise ValueError('Message cannot be blank.')
        return value.strip()

class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    linked_entity_type: str | None = Field(default=None, max_length=60)
    linked_entity_id: int | None = Field(default=None, ge=1)

    @field_validator('body')
    @classmethod
    def nonblank_body(cls, value):
        if not value.strip(): raise ValueError('Message cannot be blank.')
        return value.strip()

class TaskCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=10000)
    assigned_to: int
    branch_id: int | None = None
    conversation_id: int | None = None
    priority: Literal['low','normal','high','urgent'] = 'normal'
    due_at: datetime | None = None

class TaskStatusUpdate(BaseModel):
    status: Literal['open','in_progress','blocked','done','cancelled']

class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=10000)
    assigned_to: int | None = None
    branch_id: int | None = None
    priority: Literal['low','normal','high','urgent'] | None = None
    due_at: datetime | None = None
    status: Literal['open','in_progress','blocked','done','cancelled'] | None = None

def _role_name(db: Session, user: User):
    role = db.get(Role, user.role_id) if user.role_id else None
    return role.name if role else 'unknown'

def _user_public(db: Session, user: User):
    role = db.get(Role, user.role_id) if user.role_id else None
    return {'id': user.id, 'name': user.name, 'role': role.name if role else 'unknown'}

def _participant(db: Session, conversation_id: int, user_id: int):
    row = db.query(ConversationParticipant).filter_by(conversation_id=conversation_id, user_id=user_id).first()
    if not row: raise HTTPException(404, 'Conversation not found.')
    return row

def _conversation_summary(db: Session, c: Conversation, current: User):
    people = db.query(ConversationParticipant).filter_by(conversation_id=c.id).all()
    people_users = [db.get(User, p.user_id) for p in people]
    latest = db.query(TeamMessage).filter_by(conversation_id=c.id).order_by(TeamMessage.id.desc()).first()
    current_participant = next((p for p in people if p.user_id == current.id), None)
    unread_query = db.query(TeamMessage).filter(TeamMessage.conversation_id==c.id, TeamMessage.sender_id!=current.id)
    if current_participant and current_participant.last_read_at:
        unread_query = unread_query.filter(TeamMessage.created_at > current_participant.last_read_at)
    unread = unread_query.count() if current_participant else 0
    other_names = [u.name for u in people_users if u and u.id != current.id]
    return {'id':c.id,'subject':c.subject or ', '.join(other_names[:3]) or 'Conversation','kind':c.kind,
            'branch_id':c.branch_id,'created_by':c.created_by,'updated_at':c.updated_at.isoformat()+'Z',
            'participants':[ _user_public(db,u) for u in people_users if u],
            'last_message':({'id':latest.id,'sender_id':latest.sender_id,'body':latest.body,'created_at':latest.created_at.isoformat()+'Z'} if latest else None),
            'unread_count':unread}

def _notify(db: Session, user_id: int, kind: str, title: str, message: str):
    db.add(Notification(user_id=user_id,kind=kind,title=title,message=message[:1000],is_read=False))

def _audit(db: Session, actor_id: int, action: str, entity_type: str, entity_id: int, after: dict):
    import json
    db.add(AuditLog(user_id=actor_id,action=action,entity_type=entity_type,entity_id=entity_id,after_json=json.dumps(after,ensure_ascii=False)))

def _validate_record_link(kind: str | None, entity_id: int | None):
    allowed={'sale','invoice','purchase','purchase_order','product','stock_transfer','shift','expense','customer','supplier','service_order'}
    if bool(kind) != bool(entity_id): raise HTTPException(400,'Linked record type and ID must be supplied together.')
    if kind and kind not in allowed: raise HTTPException(400,'Unsupported linked record type.')

@router.get('/users')
def list_message_users(db: Session=Depends(get_db), user: User=Depends(current_user)):
    rows=db.query(User).filter(User.is_active==True,User.id!=user.id).order_by(User.name).all()
    return [_user_public(db,u) for u in rows]

@router.get('/conversations')
def list_conversations(db: Session=Depends(get_db), user: User=Depends(current_user)):
    ids=[x.conversation_id for x in db.query(ConversationParticipant).filter_by(user_id=user.id).all()]
    if not ids: return []
    conversations=db.query(Conversation).filter(Conversation.id.in_(ids)).order_by(Conversation.updated_at.desc(),Conversation.id.desc()).all()
    return [_conversation_summary(db,c,user) for c in conversations]

@router.post('/conversations')
def create_conversation(payload: ConversationCreate, db: Session=Depends(get_db), user: User=Depends(current_user)):
    recipients=sorted(set(payload.recipient_ids))
    if user.id in recipients: raise HTTPException(400,'Choose another team member; you are included automatically.')
    if not recipients: raise HTTPException(400,'Choose at least one recipient.')
    selected=db.query(User).filter(User.id.in_(recipients),User.is_active==True).all()
    if {u.id for u in selected} != set(recipients): raise HTTPException(400,'One or more recipients are missing or inactive.')
    if payload.branch_id is not None:
        b=db.query(Branch).filter(Branch.id==payload.branch_id,Branch.is_active==True).first()
        if not b: raise HTTPException(400,'Branch not found or inactive.')
    _validate_record_link(payload.linked_entity_type,payload.linked_entity_id)
    kind='direct' if len(recipients)==1 else 'group'
    conversation=None
    if kind=='direct':
        for candidate in db.query(Conversation).filter(Conversation.kind=='direct').join(ConversationParticipant).filter(ConversationParticipant.user_id==user.id).all():
            members={p.user_id for p in db.query(ConversationParticipant).filter_by(conversation_id=candidate.id).all()}
            if members=={user.id,recipients[0]}:
                conversation=candidate; break
    if not conversation:
        conversation=Conversation(subject=(payload.subject or '').strip() or None,kind=kind,branch_id=payload.branch_id,created_by=user.id,created_at=now_utc(),updated_at=now_utc())
        db.add(conversation); db.flush()
        for uid in sorted([user.id,*recipients]):
            db.add(ConversationParticipant(conversation_id=conversation.id,user_id=uid,joined_at=now_utc(),last_read_at=now_utc() if uid==user.id else None))
    elif payload.subject and payload.subject.strip() and not conversation.subject:
        conversation.subject=payload.subject.strip()
    message=TeamMessage(conversation_id=conversation.id,sender_id=user.id,body=payload.initial_message,linked_entity_type=payload.linked_entity_type,linked_entity_id=payload.linked_entity_id,created_at=now_utc())
    db.add(message); conversation.updated_at=now_utc()
    for uid in recipients:
        _notify(db,uid,'message',f'New message from {user.name}',payload.initial_message[:240])
    _audit(db,user.id,'team_message_sent','conversation',conversation.id,{'recipient_ids':recipients,'message_id':None,'kind':kind})
    db.commit(); db.refresh(conversation); db.refresh(message)
    return {'conversation':_conversation_summary(db,conversation,user),'message':{'id':message.id,'conversation_id':message.conversation_id,'sender_id':message.sender_id,'body':message.body,'linked_entity_type':message.linked_entity_type,'linked_entity_id':message.linked_entity_id,'created_at':message.created_at.isoformat()+'Z'}}

@router.get('/conversations/{conversation_id}/messages')
def list_messages(conversation_id: int, limit: int=200, db: Session=Depends(get_db), user: User=Depends(current_user)):
    participant=_participant(db,conversation_id,user.id)
    participant.last_read_at=now_utc(); db.commit()
    limit=max(1,min(limit,500))
    rows=db.query(TeamMessage).filter_by(conversation_id=conversation_id).order_by(TeamMessage.id.desc()).limit(limit).all()[::-1]
    people=db.query(ConversationParticipant).filter_by(conversation_id=conversation_id).all()
    result=[]
    for m in rows:
        read_by=[p.user_id for p in people if p.user_id!=m.sender_id and p.last_read_at and p.last_read_at>=m.created_at]
        result.append({'id':m.id,'conversation_id':m.conversation_id,'sender_id':m.sender_id,'sender_name':db.get(User,m.sender_id).name if db.get(User,m.sender_id) else 'Unknown',
                       'body':m.body,'linked_entity_type':m.linked_entity_type,'linked_entity_id':m.linked_entity_id,'created_at':m.created_at.isoformat()+'Z',
                       'delivered_to':[p.user_id for p in people if p.user_id!=m.sender_id],'read_by':read_by})
    return result

@router.post('/conversations/{conversation_id}/messages')
def send_message(conversation_id: int, payload: MessageCreate, db: Session=Depends(get_db), user: User=Depends(current_user)):
    _participant(db,conversation_id,user.id)
    _validate_record_link(payload.linked_entity_type,payload.linked_entity_id)
    c=db.get(Conversation,conversation_id)
    if not c: raise HTTPException(404,'Conversation not found.')
    msg=TeamMessage(conversation_id=c.id,sender_id=user.id,body=payload.body,linked_entity_type=payload.linked_entity_type,linked_entity_id=payload.linked_entity_id,created_at=now_utc())
    db.add(msg); c.updated_at=now_utc()
    participants=db.query(ConversationParticipant).filter_by(conversation_id=c.id).all()
    for p in participants:
        if p.user_id!=user.id: _notify(db,p.user_id,'message',f'New message from {user.name}',payload.body[:240])
    _audit(db,user.id,'team_message_sent','conversation',c.id,{'message_length':len(payload.body),'linked_entity_type':payload.linked_entity_type,'linked_entity_id':payload.linked_entity_id})
    db.commit(); db.refresh(msg)
    return {'id':msg.id,'conversation_id':msg.conversation_id,'sender_id':msg.sender_id,'sender_name':user.name,'body':msg.body,'linked_entity_type':msg.linked_entity_type,'linked_entity_id':msg.linked_entity_id,'created_at':msg.created_at.isoformat()+'Z'}

@router.get('/tasks')
def list_tasks(db: Session=Depends(get_db), user: User=Depends(current_user)):
    rows=db.query(TeamTask).filter((TeamTask.created_by==user.id)|(TeamTask.assigned_to==user.id)).order_by(TeamTask.due_at.is_(None),TeamTask.due_at.asc(),TeamTask.created_at.desc()).limit(500).all()
    return [_task_dict(db,t) for t in rows]

def _task_dict(db: Session, t: TeamTask):
    creator=db.get(User,t.created_by); assignee=db.get(User,t.assigned_to)
    return {'id':t.id,'title':t.title,'description':t.description,'created_by':t.created_by,'creator_name':creator.name if creator else 'Unknown',
            'assigned_to':t.assigned_to,'assignee_name':assignee.name if assignee else 'Unknown','branch_id':t.branch_id,'conversation_id':t.conversation_id,
            'priority':t.priority,'status':t.status,'due_at':t.due_at.isoformat()+'Z' if t.due_at else None,'created_at':t.created_at.isoformat()+'Z',
            'updated_at':t.updated_at.isoformat()+'Z','completed_at':t.completed_at.isoformat()+'Z' if t.completed_at else None}

@router.post('/tasks')
def create_task(payload: TaskCreate, db: Session=Depends(get_db), user: User=Depends(current_user)):
    assignee=db.query(User).filter_by(id=payload.assigned_to,is_active=True).first()
    if not assignee: raise HTTPException(400,'Assigned user is missing or inactive.')
    if payload.branch_id is not None and not db.query(Branch).filter_by(id=payload.branch_id,is_active=True).first(): raise HTTPException(400,'Branch not found or inactive.')
    if payload.conversation_id is not None:
        _participant(db,payload.conversation_id,user.id)
        if not db.query(ConversationParticipant).filter_by(conversation_id=payload.conversation_id,user_id=assignee.id).first():
            raise HTTPException(400,'When a task is linked to a conversation, the assignee must be a participant in that conversation.')
    due=payload.due_at
    if due and due.tzinfo: due=due.astimezone(timezone.utc).replace(tzinfo=None)
    task=TeamTask(title=payload.title.strip(),description=(payload.description or '').strip() or None,created_by=user.id,assigned_to=assignee.id,branch_id=payload.branch_id,conversation_id=payload.conversation_id,priority=payload.priority,status='open',due_at=due,created_at=now_utc(),updated_at=now_utc())
    db.add(task); db.flush()
    if assignee.id!=user.id: _notify(db,assignee.id,'task',f'Task assigned by {user.name}',task.title)
    _audit(db,user.id,'team_task_created','team_task',task.id,{'title':task.title,'assigned_to':task.assigned_to,'priority':task.priority})
    db.commit(); db.refresh(task)
    return _task_dict(db,task)

@router.put('/tasks/{task_id}')
def update_task(task_id: int, payload: TaskUpdate, db: Session=Depends(get_db), user: User=Depends(current_user)):
    task=db.get(TeamTask,task_id)
    if not task: raise HTTPException(404,'Task not found.')
    role=_role_name(db,user)
    if user.id not in (task.created_by,task.assigned_to) and role not in ('owner','admin','manager'):
        raise HTTPException(403,'Only the task creator, assignee or management may update this task.')
    data=payload.model_dump(exclude_unset=True)
    old_assignee=task.assigned_to
    if 'assigned_to' in data and data['assigned_to'] is not None:
        assignee=db.query(User).filter_by(id=data['assigned_to'],is_active=True).first()
        if not assignee: raise HTTPException(400,'Assigned user is missing or inactive.')
        task.assigned_to=assignee.id
    if 'branch_id' in data:
        if data['branch_id'] is not None and not db.query(Branch).filter_by(id=data['branch_id'],is_active=True).first(): raise HTTPException(400,'Branch not found or inactive.')
        task.branch_id=data['branch_id']
    for key in ('title','description','priority','status'):
        if key in data and data[key] is not None: setattr(task,key,data[key].strip() if key in ('title','description') and isinstance(data[key],str) else data[key])
    if 'due_at' in data:
        due=data['due_at']
        if due and due.tzinfo: due=due.astimezone(timezone.utc).replace(tzinfo=None)
        task.due_at=due
    if task.status=='done': task.completed_at=now_utc()
    elif 'status' in data: task.completed_at=None
    task.updated_at=now_utc()
    if task.assigned_to!=old_assignee and task.assigned_to!=user.id: _notify(db,task.assigned_to,'task',f'Task assigned by {user.name}',task.title)
    _audit(db,user.id,'team_task_updated','team_task',task.id,{'status':task.status,'assigned_to':task.assigned_to})
    db.commit(); db.refresh(task)
    return _task_dict(db,task)
