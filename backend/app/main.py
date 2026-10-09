from pathlib import Path
from contextlib import asynccontextmanager
import sys
from datetime import date
from decimal import Decimal
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from app.core.config import settings
from app.core.database import Base, engine, get_db
from app.models import models
from app.schemas.common import ProductCreate, SaleCreate
from app.services.sales import create_sale
from app.reports.analytics import monthly_sales_report
from app.reports.dashboard import dashboard_summary
from app.api.routes import router as business_router
from app.api.platform import router as platform_router
from app.api.enterprise import router as enterprise_router
from app.api.cloud import router as cloud_router
from app.api.ai import router as ai_router
from app.api.system import router as system_router, seed_roles
from app.api.communications import router as communications_router
from app.services.accounting import seed_accounts
from app.reports.reporting import *
from app.core.security import current_user, require_roles
from alembic.config import Config
from alembic import command
from app.services.backup import backup_database, restore_database, latest_backups, BACKUP_DIR
import shutil

APP_VERSION='1.10.2'


def seed_platform_defaults(db):
    from app.models.models import Branch, Warehouse, PriceList, PaymentMethod
    if not db.query(Branch).count():
        db.add(Branch(code='MAIN', name='Main Branch', is_default=True))
        db.flush()
    branch=db.query(Branch).filter(Branch.is_default==True, Branch.is_active==True).order_by(Branch.id).first() or db.query(Branch).filter(Branch.is_active==True).order_by(Branch.id).first()
    if branch and not db.query(Warehouse).count():
        db.add(Warehouse(code='MAIN', name='Main Warehouse', branch_id=branch.id, is_default=True))
    if not db.query(PriceList).filter(PriceList.is_default==True, PriceList.is_active==True).first():
        from app.models.models import BusinessProfile
        business = db.query(BusinessProfile).order_by(BusinessProfile.id).first()
        currency = (getattr(business, 'currency', None) or 'TZS').upper()
        db.add(PriceList(name='Retail', code='RETAIL', currency=currency, is_default=True, is_active=True))
    db.flush()

@asynccontextmanager
async def lifespan(app):
    project_root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
    alembic_ini=project_root/'alembic.ini'
    if alembic_ini.exists():
        cfg=Config(str(alembic_ini)); cfg.set_main_option('script_location',str(project_root/'backend'/'migrations'))
        # Protect existing PostgreSQL data only when an upgrade/bootstrap migration is pending.
        if engine.dialect.name == 'postgresql':
            try:
                from sqlalchemy import inspect
                from alembic.runtime.migration import MigrationContext
                from alembic.script import ScriptDirectory
                existing_tables = inspect(engine).get_table_names()
                has_version = 'alembic_version' in existing_tables
                current_revision = None
                if has_version:
                    with engine.begin() as conn:
                        conn.execute(text(
                            "UPDATE alembic_version SET version_num=:new WHERE version_num=:old"
                        ), {
                            'new': '0010_profile_reconcile',
                            'old': '0010_customer_supplier_profile_reconciliation',
                        })
                    with engine.connect() as conn:
                        current_revision = MigrationContext.configure(conn).get_current_revision()
                current_head = ScriptDirectory.from_config(cfg).get_current_head()
                migration_pending = bool(existing_tables) and ((not has_version) or current_revision != current_head)
                if migration_pending:
                    backup_database(engine, reason='pre-migration')
            except Exception as exc:
                raise RuntimeError(f'CODEZONE stopped before database migration because the safety backup check failed: {exc}') from exc
        try:
            command.upgrade(cfg,'head')
        except Exception as exc:
            # PostgreSQL migrations are transactional where supported; attempt a last-resort restore from the newest pre-migration dump.
            if engine.dialect.name == 'postgresql':
                candidates=sorted(BACKUP_DIR.glob('CODEZONE-pre-migration-*.dump'), key=lambda p:p.stat().st_mtime, reverse=True)
                if candidates:
                    try: restore_database(engine, candidates[0])
                    except Exception: pass
            raise RuntimeError(f'CODEZONE database migration failed. The application was stopped safely: {exc}') from exc
    else:
        Base.metadata.create_all(bind=engine)
    db=next(get_db())
    try:
        seed_accounts(db)
        seed_roles(db)
        seed_platform_defaults(db)
        db.commit()
    finally: db.close()
    yield

app=FastAPI(title=settings.app_name,version=APP_VERSION,lifespan=lifespan)
# Desktop-only API: default to same-origin; extra origins can be configured explicitly.
_port=__import__('os').environ.get('CODEZONE_PORT','8765')
allowed_origins=[x.strip() for x in __import__('os').environ.get('CODEZONE_ALLOWED_ORIGINS',f'http://127.0.0.1:{_port},http://localhost:{_port}').split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=True, allow_methods=['GET','POST','PUT','DELETE','OPTIONS'], allow_headers=['Authorization','Content-Type'])

@app.get('/health')
def health(): return {'status':'ok','app':settings.app_name,'version':app.version}

@app.get('/api/backups')
def list_backups(_:models.User=Depends(require_roles('owner','admin'))):
    return latest_backups()

@app.post('/api/backups')
def create_backup(_:models.User=Depends(require_roles('owner','admin'))):
    try:
        p=backup_database(engine, reason='manual')
        if not p: raise HTTPException(400,'Backup is available only when PostgreSQL is configured.')
        return {'name':p.name,'path':str(p),'size':p.stat().st_size}
    except Exception as exc:
        raise HTTPException(500, f'Backup failed: {exc}')

@app.post('/api/backups/restore')
def restore_backup(payload: dict, db:Session=Depends(get_db), _:models.User=Depends(require_roles('owner','admin'))):
    name=str(payload.get('name') or '').strip()
    if not name or Path(name).name != name or not name.endswith('.dump'):
        raise HTTPException(400,'Invalid backup file.')
    p=BACKUP_DIR/name
    if not p.exists():
        raise HTTPException(404,'Backup file not found.')
    try:
        # Release every application-held database connection before pg_restore --clean.
        db.rollback()
        db.close()
        engine.dispose()
        restore_database(engine,p)
        return {'status':'ok','message':'Backup restored successfully. Restart CODEZONE before continuing work.'}
    except Exception as exc:
        raise HTTPException(500, f'Restore failed: {exc}')

@app.get('/api/reports/monthly-sales')
def report(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(current_user)): return monthly_sales_report(db,start_date,end_date)
@app.get('/api/accounting/trial-balance')
def trial_balance(start_date:str|None=None,end_date:str|None=None,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','accountant'))):
    from app.services.accounting import trial_balance as tb
    return tb(db,date.fromisoformat(start_date) if start_date else None,date.fromisoformat(end_date) if end_date else None)
@app.get('/api/accounting/profit-loss')
def profit_loss(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','accountant','manager'))):
    from app.services.accounting import profit_loss as pl
    return pl(db,date.fromisoformat(start_date),date.fromisoformat(end_date))
@app.get('/api/accounting/accounts')
def accounts(db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','accountant'))):
    return [{'code':a.code,'name':a.name,'account_type':a.account_type,'active':a.is_active} for a in db.query(models.Account).order_by(models.Account.code).all()]
@app.get('/api/dashboard')
def dashboard(db:Session=Depends(get_db),user:models.User=Depends(require_roles('owner','admin','manager','salesperson','storekeeper','accountant','cashier'))):
    role=db.get(models.Role,user.role_id) if user.role_id else None
    class RoleProxy: pass
    proxy=RoleProxy(); proxy.name=role.name if role else None
    user.role=proxy
    return dashboard_summary(db,user)
@app.post('/api/products')
def products(payload:ProductCreate,db:Session=Depends(get_db),actor:models.User=Depends(require_roles('owner','admin','manager','storekeeper'))):
    sku=payload.sku.strip(); name=payload.name.strip()
    if not sku: raise HTTPException(400,'SKU is required.')
    if not name: raise HTTPException(400,'Product name is required.')
    if db.query(models.Product).filter(models.Product.sku==sku).first():
        raise HTTPException(409,'SKU already exists.')
    if payload.selling_price <= 0:
        raise HTTPException(400,'Selling price must be greater than zero.')
    if payload.selling_price < payload.buying_price:
        raise HTTPException(400,'Selling price cannot be below buying price.')
    try:
        data=payload.model_dump(); data['sku']=sku; data['name']=name; data['item_type']=str(data.get('item_type') or 'product'); data['track_inventory']=False if data['item_type']=='service' else bool(data.get('track_inventory',True)); p=models.Product(**data)
        db.add(p); db.flush()
        if p.current_stock:
            db.add(models.StockMovement(product_id=p.id,movement_type='opening_stock',quantity=p.current_stock,reference_type='product',reference_id=p.id))
        db.add(models.AuditLog(user_id=actor.id,action='product_created',entity_type='product',entity_id=p.id,
                                after_json=str({'sku':p.sku,'name':p.name,'buying_price':str(p.buying_price),'selling_price':str(p.selling_price),'current_stock':str(p.current_stock)})))
        db.commit(); db.refresh(p)
        return {'id':p.id,'sku':p.sku,'name':p.name,'buying_price':str(p.buying_price),'selling_price':str(p.selling_price),'stock':str(p.current_stock),'minimum_stock':str(p.minimum_stock),'reorder_level':str(p.reorder_level)}
    except HTTPException:
        db.rollback(); raise
    except IntegrityError as exc:
        db.rollback();
        message='SKU already exists.' if 'sku' in str(exc).lower() else ('Barcode already exists.' if 'barcode' in str(exc).lower() else 'Product could not be saved because a unique field already exists.')
        raise HTTPException(409,message)
@app.post('/api/sales')
def sales(payload:SaleCreate,db:Session=Depends(get_db),user:models.User=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    role=db.get(models.Role,user.role_id) if user.role_id else None
    role_name=role.name if role else ''
    if payload.allow_below_cost and role_name not in ('owner','admin','manager'):
        raise HTTPException(403,'Only management can approve a sale below cost.')
    subtotal=sum((Decimal(str(x.quantity))*Decimal(str(x.unit_price)) for x in payload.items),Decimal('0'))
    discount_pct=(payload.discount/subtotal*100) if subtotal else Decimal('0')
    if discount_pct > Decimal('5') and role_name not in ('owner','admin','manager'):
        raise HTTPException(403,'Discount above 5% requires manager approval.')
    try:
        result=create_sale(db,payload,user.id)
        if payload.allow_below_cost:
            db.add(models.AuditLog(user_id=user.id,action='sale_below_cost_override',entity_type='sale',entity_id=result.id,after_json=str({'invoice_no':result.invoice_no})))
            db.commit(); db.refresh(result)
        return result
    except ValueError as e: raise HTTPException(status_code=400,detail=str(e))

@app.get('/api/sales/{sale_id}/pdf')
def sale_pdf(sale_id:int, db:Session=Depends(get_db), _:models.User=Depends(current_user)):
    sale=db.get(models.Sale,sale_id)
    if not sale: raise HTTPException(404,'Sale not found.')
    customer=db.get(models.Customer,sale.customer_id) if sale.customer_id else None
    profile=db.get(models.BusinessProfile,1)
    items=db.query(models.SaleItem).filter(models.SaleItem.sale_id==sale.id).all()
    products={p.id:p for p in db.query(models.Product).filter(models.Product.id.in_([i.product_id for i in items])).all()} if items else {}
    buf=BytesIO(); pdf=canvas.Canvas(buf,pagesize=A4); width,height=A4
    left=18*mm; y=height-18*mm
    pdf.setTitle(f'Invoice {sale.invoice_no}')
    business_name=profile.business_name if profile else settings.app_name
    pdf.setFont('Helvetica-Bold',16); pdf.drawString(left,y,business_name); y-=6*mm
    if profile:
        pdf.setFont('Helvetica',8)
        contact=' | '.join([x for x in [profile.phone, profile.email, profile.address] if x])
        if contact: pdf.drawString(left,y,contact[:110]); y-=5*mm
        ids=' | '.join([x for x in [f'TIN: {profile.tin}' if profile.tin else '', f'VAT: {profile.vat_number}' if profile.vat_number else ''] if x])
        if ids: pdf.drawString(left,y,ids[:110]); y-=5*mm
    pdf.setFont('Helvetica-Bold',12); pdf.drawString(left,y,f'INVOICE: {sale.invoice_no}'); y-=6*mm
    pdf.setFont('Helvetica',9); pdf.drawString(left,y,f'Date: {sale.sale_date.strftime("%Y-%m-%d %H:%M")}'); y-=5*mm
    pdf.drawString(left,y,f'Customer: {customer.name if customer else "Walk-in customer"}'); y-=9*mm
    pdf.setFont('Helvetica-Bold',9); pdf.drawString(left,y,'Product'); pdf.drawString(115*mm,y,'Qty'); pdf.drawString(135*mm,y,'Unit price'); pdf.drawString(165*mm,y,'Line total'); y-=5*mm
    pdf.setFont('Helvetica',9)
    for item in items:
        product=products.get(item.product_id); name=(product.name if product else 'Unknown')[:48]
        pdf.drawString(left,y,name); pdf.drawRightString(128*mm,y,f'{Decimal(item.quantity):,.3f}'.rstrip('0').rstrip('.')); pdf.drawRightString(160*mm,y,f'{Decimal(item.unit_price):,.2f}'); pdf.drawRightString(195*mm,y,f'{Decimal(item.line_total):,.2f}'); y-=5*mm
        if y<30*mm: pdf.showPage(); y=height-20*mm; pdf.setFont('Helvetica',9)
    y-=3*mm; pdf.line(left,y,195*mm,y); y-=6*mm
    for label,value in [('Subtotal',sale.subtotal),('Discount',sale.discount),('Total',sale.total),('Paid',sale.paid),('Balance',max(Decimal('0'),Decimal(sale.total)-Decimal(sale.paid)))]:
        pdf.drawRightString(160*mm,y,label+':'); pdf.drawRightString(195*mm,y,f'{(profile.currency if profile else "TZS")} {Decimal(value):,.2f}'); y-=5*mm
    pdf.setFont('Helvetica',7); pdf.drawString(left,20*mm,f'Generated by {business_name}')
    pdf.save(); data=buf.getvalue(); buf.close()
    return Response(content=data,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{sale.invoice_no}.pdf"'})
app.include_router(business_router)
app.include_router(platform_router)
app.include_router(enterprise_router)
app.include_router(cloud_router)
app.include_router(ai_router)
app.include_router(communications_router)
app.include_router(system_router)

def parse_dates(start_date:str,end_date:str):
    try:
        s,e=date.fromisoformat(start_date),date.fromisoformat(end_date)
        if e<s: raise ValueError('end_date must be on or after start_date')
        return s,e
    except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/reports/summary')
def report_summary(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); return period_summary(db,s,e)
@app.get('/api/reports/sales')
def report_sales(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); return sales_report(db,s,e)
@app.get('/api/reports/purchases')
def report_purchases(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); return purchase_report(db,s,e)
@app.get('/api/reports/expenses')
def report_expenses(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); return expense_report(db,s,e)
@app.get('/api/reports/product-sales')
def report_product_sales(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); return product_sales_report(db,s,e)
@app.get('/api/reports/stock')
def report_stock(db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant','storekeeper'))): return stock_report(db)
@app.get('/api/reports/customer-aging')
def report_customer_aging(db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant','salesperson','cashier'))):
    from app.services.transactions import aging
    return aging(db,'customer')
@app.get('/api/reports/supplier-aging')
def report_supplier_aging(db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant','storekeeper'))):
    from app.services.transactions import aging
    return aging(db,'supplier')
@app.get('/api/reports/cash-book')
def report_cash_book(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','accountant'))):
    s,e=parse_dates(start_date,end_date); return cash_book_report(db,s,e)
@app.get('/api/reports/bank-statement')
def report_bank(start_date:str,end_date:str,bank_account_id:int|None=None,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','accountant'))):
    s,e=parse_dates(start_date,end_date); return bank_statement_report(db,s,e,bank_account_id)
@app.get('/api/reports/stock-ledger')
def report_stock_ledger(start_date:str,end_date:str,product_id:int|None=None,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant','storekeeper'))):
    s,e=parse_dates(start_date,end_date); return stock_ledger_report(db,s,e,product_id)
@app.get('/api/reports/margins')
def report_margins(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); return margin_report(db,s,e)
@app.get('/api/reports/end-of-day')
def report_end_of_day(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); return end_of_day_report(db,s,e)

@app.get('/api/accounting/balance-sheet')
def balance_sheet(db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','accountant','manager'))):
    return balance_sheet_report(db)

@app.get('/api/reports/audit-log')
def report_audit(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin'))):
    s,e=parse_dates(start_date,end_date); return audit_report(db,s,e)
@app.get('/api/reports/customer-debt')
def report_customer_debt(db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant','salesperson','cashier'))):
    return customer_debt_report(db)
@app.get('/api/reports/supplier-debt')
def report_supplier_debt(db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant','storekeeper'))):
    return supplier_debt_report(db)
@app.get('/api/reports/trial-balance')
def report_trial_balance(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','accountant'))):
    s,e=parse_dates(start_date,end_date); return trial_balance_report(db,s,e)
@app.get('/api/reports/profit-loss')
def report_profit_loss(start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); return profit_loss_report(db,s,e)

_ALLOWED_EXPORTS={'summary','sales','purchases','expenses','product-sales','stock','margins','cash-book','stock-ledger','audit-log','bank-statement','customer-debt','supplier-debt','trial-balance','profit-loss','end-of-day','balance-sheet'}

def _export_data(report,start_date,end_date,db):
    if report not in _ALLOWED_EXPORTS:
        raise HTTPException(400, f'Unknown report: {report}.')
    try:
        return excel_bytes(db,report,start_date,end_date)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc

@app.get('/api/reports/export/excel')
def export_excel(report:str,start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date); data=_export_data(report,s,e,db); return Response(content=data,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="codezone-{report}.xlsx"'})
@app.get('/api/reports/export/pdf')
def export_pdf(report:str,start_date:str,end_date:str,db:Session=Depends(get_db),_:models.User=Depends(require_roles('owner','admin','manager','accountant'))):
    s,e=parse_dates(start_date,end_date)
    if report not in _ALLOWED_EXPORTS:
        raise HTTPException(400, f'Unknown report: {report}.')
    try:
        data=pdf_bytes(db,report,s,e)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    return Response(content=data,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="codezone-{report}.pdf"'})

_frontend = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))/'desktop'/'frontend'
if _frontend.exists(): app.mount('/',StaticFiles(directory=str(_frontend),html=True),name='frontend')

