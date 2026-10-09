from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text, func
from sqlalchemy.orm import Session

from app.core.security import current_user, require_roles
from app.core.database import get_db
from app.models import models
from app.services.transactions import aging

router = APIRouter(prefix="/api/ai", tags=["ai"])
READ_ROLES = ("owner", "admin", "manager", "accountant", "salesperson", "cashier", "storekeeper")

logger = logging.getLogger("codezone.ai")
_AI_STATE_LOCK = threading.Lock()
_AI_STATE: dict[str, Any] = {"remote_available": None, "last_error": None, "last_failure_at": None, "data_context_warning": None}

# Common everyday and business Kiswahili terms; language should not switch to English just
# because the user did not use one of a few exact phrases.
SW_MARKERS = (
    "habari", "mambo", "tafadhali", "naomba", "nisaidie", "nifanye", "nifanyeje",
    "mauzo", "mwezi", "wiki", "jana", "kesho", "nionyeshe", "nipe", "ushauri",
    "biashara", "faida", "hasara", "deni", "madeni", "bidhaa", "stoo", "bei",
    "wateja", "mapato", "gharama", "matumizi", "nunua", "nunue", "uagizaji",
    "muhtasari", "kiasi gani", "inaendaje", "ikoje", "ninapaswa", "nifanye nini",
    "ninawezaje", "nipate", "kuongeza", "kupunguza", "kiasi", "niambie", "kwa nini",
    "lini", "nani", "gani", "zangu", "yangu", "yetu", "naomba", "asante",
)
EN_MARKERS = (
    "hello", "hi", "good morning", "good afternoon", "today", "month", "week", "yesterday",
    "show me", "give me", "advice", "business", "profit", "loss", "debt", "customers",
    "inventory", "stock", "sales", "profit", "cash", "supplier", "discount", "reorder", "invoice", "margin", "report", "purchase", "expenses", "what should", "how can", "please", "help me",
    "why", "when", "which", "how much", "how many", "my business", "my sales", "tell me",
)

KNOWLEDGE = {
    "sales": {
        "sw": "Linganisha mwenendo wa mauzo, wastani wa order, discounts, returns na utegemezi wa top customers/products. Ukuaji wa mauzo bila margin unaweza kupunguza faida.",
        "en": "Assess sales trend, average order value, discounts, returns and concentration in top customers/products. Revenue growth without margin can still reduce profit.",
    },
    "inventory": {
        "sw": "Tenganisha bidhaa zinazokaribia kuishiwa na slow movers. Reorder inapaswa kuangalia kasi ya mauzo, lead time na cash available, si reorder level pekee.",
        "en": "Separate stockout risk from slow movers. Reordering should consider sales velocity, lead time and available cash, not reorder level alone.",
    },
    "receivables": {
        "sw": "Deni ni pressure ya cash. Panga collections kwa kiasi, umri wa deni, credit utilization na historia ya malipo; linda credit limit.",
        "en": "Receivables create cash pressure. Prioritize collections by amount, age, credit utilization and payment behavior; protect the credit limit.",
    },
    "margin": {
        "sw": "Gross margin ndiyo kinga ya msingi ya biashara. Chunguza products zenye margin ndogo, discounts kubwa, cost drift na returns kabla ya kuongeza volume.",
        "en": "Gross margin is a core protection for the business. Check low-margin products, heavy discounts, cost drift and returns before chasing volume.",
    },
    "cash": {
        "sw": "Sales si sawa na cash. Angalia paid sales, receivables, supplier commitments na expenses ili kuamua kama cash is available for reordering or expansion.",
        "en": "Sales are not the same as cash. Consider paid sales, receivables, supplier commitments and expenses before committing cash to reorders or expansion.",
    },
    "operations": {
        "sw": "Operational discipline inahusisha shifts, approvals, pending deliveries, returns na stock adjustments. Exceptions ndizo zinaweza kuleta leakage.",
        "en": "Operational discipline covers shifts, approvals, pending deliveries, returns and stock adjustments. Exceptions are where leakage often appears.",
    },
}


def _read_ai_setting(name: str, default: str = "") -> str:
    """Read a single AI setting from process environment or CODEZONE config files.

    The installer writes settings to %PROGRAMDATA%\\CODEZONE\\config.env. Reading only
    os.environ made the remote AI provider invisible when configured in that file.
    Process environment wins over config file values.
    """
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    candidates: list[Path] = []
    explicit = os.environ.get("CODEZONE_CONFIG_FILE")
    if explicit:
        candidates.append(Path(explicit))
    program_data = Path(os.environ.get("PROGRAMDATA", str(Path.home())))
    candidates.append(program_data / "CODEZONE" / "config.env")
    candidates.append(Path.cwd() / ".env")
    # Also support source development when the current directory is not the project root.
    candidates.append(Path(__file__).resolve().parents[3] / ".env")
    seen: set[str] = set()
    for file_path in candidates:
        try:
            marker = str(file_path.resolve())
            if marker in seen:
                continue
            seen.add(marker)
            if not file_path.is_file():
                continue
            for raw_line in file_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw_value = line.split("=", 1)
                if key.strip() != name:
                    continue
                result = raw_value.strip()
                if len(result) >= 2 and result[0] == result[-1] and result[0] in {"'", '"'}:
                    result = result[1:-1]
                return result.strip()
        except (OSError, UnicodeError):
            continue
    return default


def _provider_config() -> dict[str, Any]:
    api_key = _read_ai_setting("CODEZONE_AI_API_KEY")
    explicit_provider = _read_ai_setting("CODEZONE_AI_PROVIDER").strip().lower()
    provider = explicit_provider or ("openai" if api_key else "local")
    model = _read_ai_setting("CODEZONE_AI_MODEL").strip()
    if not model:
        model = "qwen2.5:7b" if provider == "ollama" else "gpt-5.6-terra"
    default_base = "http://127.0.0.1:11434/v1" if provider == "ollama" else "https://api.openai.com/v1"
    base_url = _read_ai_setting("CODEZONE_AI_BASE_URL", default_base).rstrip("/")
    # Never forward a saved OpenAI API key to a local model provider.
    # Ollama's OpenAI-compatible endpoint accepts a placeholder bearer token.
    if provider == "ollama":
        api_key = "ollama"
    try:
        timeout = max(10, min(90, int(_read_ai_setting("CODEZONE_AI_TIMEOUT_SECONDS", "45"))))
    except ValueError:
        timeout = 45
    configured = (provider == "ollama") or bool(api_key and provider not in {"local", "none", "disabled"})
    return {"provider": provider, "model": model or "gpt-5.6-terra", "base_url": base_url,
            "configured": configured, "api_key": api_key, "timeout": timeout}


def _money(v: Any) -> float:
    try:
        return float(Decimal(str(v or 0)))
    except Exception:
        return 0.0


def _pct(part: float, total: float) -> float:
    return round((part / total) * 100, 1) if total else 0.0


def _detect_language(message: str, requested: str | None = None) -> str:
    if requested in {"sw", "en"}:
        return requested
    q = re.sub(r"\s+", " ", message.lower()).strip()
    # Favor word/phrase matches so short fragments such as "hi" inside another word
    # do not force an English response. Multiword markers still match naturally.
    sw = sum(1 for marker in SW_MARKERS if re.search(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", q))
    en = sum(1 for marker in EN_MARKERS if re.search(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", q))
    return "sw" if sw > 0 and sw >= en else "en"


def _periods():
    today = date.today()
    start_month = today.replace(day=1)
    prev_month_end = start_month - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    start_week = today - timedelta(days=today.weekday())
    yesterday = today - timedelta(days=1)
    return today, start_month, prev_month_start, prev_month_end, start_week, yesterday


def _business_context(db: Session, user: models.User, page: str | None = None) -> dict[str, Any]:
    today, start_month, prev_month_start, prev_month_end, start_week, yesterday = _periods()
    month_start_dt = datetime.combine(start_month, datetime.min.time())
    prev_start_dt = datetime.combine(prev_month_start, datetime.min.time())
    prev_end_dt = datetime.combine(start_month, datetime.min.time())
    previous_month_days = (prev_month_end - prev_month_start).days + 1
    previous_mtd_end_date = prev_month_start + timedelta(days=min(today.day, previous_month_days))
    previous_mtd_end_dt = datetime.combine(previous_mtd_end_date, datetime.min.time())
    week_start_dt = datetime.combine(start_week, datetime.min.time())
    today_start_dt = datetime.combine(today, datetime.min.time())
    tomorrow_start_dt = today_start_dt + timedelta(days=1)

    profile = db.query(models.BusinessProfile).order_by(models.BusinessProfile.id).first()
    currency = getattr(profile, "currency", "TZS")
    industry = getattr(profile, "industry_profile", "business")
    role = db.get(models.Role, user.role_id).name if user.role_id else None

    sales_rows = db.execute(text("""
        SELECT
          COALESCE(SUM(CASE WHEN sale_date >= :today_start AND sale_date < :tomorrow THEN total ELSE 0 END),0) AS today_total,
          COUNT(CASE WHEN sale_date >= :today_start AND sale_date < :tomorrow THEN 1 END) AS today_invoices,
          COALESCE(SUM(CASE WHEN sale_date >= :week_start AND sale_date < :tomorrow THEN total ELSE 0 END),0) AS week_total,
          COUNT(CASE WHEN sale_date >= :week_start AND sale_date < :tomorrow THEN 1 END) AS week_invoices,
          COALESCE(SUM(CASE WHEN sale_date >= :month_start AND sale_date < :tomorrow THEN total ELSE 0 END),0) AS month_total,
          COUNT(CASE WHEN sale_date >= :month_start AND sale_date < :tomorrow THEN 1 END) AS month_invoices,
          COALESCE(SUM(CASE WHEN sale_date >= :prev_start AND sale_date < :prev_end THEN total ELSE 0 END),0) AS prev_month_total,
          COUNT(CASE WHEN sale_date >= :prev_start AND sale_date < :prev_end THEN 1 END) AS prev_month_invoices,
          COALESCE(SUM(CASE WHEN sale_date >= :prev_start AND sale_date < :prev_mtd_end THEN total ELSE 0 END),0) AS prev_mtd_total,
          COUNT(CASE WHEN sale_date >= :prev_start AND sale_date < :prev_mtd_end THEN 1 END) AS prev_mtd_invoices,
          COALESCE(SUM(CASE WHEN sale_date >= :month_start AND sale_date < :tomorrow THEN discount ELSE 0 END),0) AS month_discount,
          COALESCE(SUM(CASE WHEN sale_date >= :month_start AND sale_date < :tomorrow THEN subtotal ELSE 0 END),0) AS month_subtotal,
          COALESCE(SUM(CASE WHEN sale_date >= :month_start AND sale_date < :tomorrow THEN paid ELSE 0 END),0) AS month_paid
        FROM sales WHERE status='posted'
    """), {"today_start": today_start_dt, "tomorrow": tomorrow_start_dt, "week_start": week_start_dt, "month_start": month_start_dt, "prev_start": prev_start_dt, "prev_end": prev_end_dt, "prev_mtd_end": previous_mtd_end_dt}).mappings().one()

    profit_rows = db.execute(text("""
        SELECT
          COALESCE(SUM(CASE WHEN s.sale_date >= :month_start AND s.sale_date < :tomorrow THEN si.quantity*si.cost_price ELSE 0 END),0) AS month_cost,
          COALESCE(SUM(CASE WHEN s.sale_date >= :prev_start AND s.sale_date < :prev_end THEN si.quantity*si.cost_price ELSE 0 END),0) AS prev_cost
        FROM sales s JOIN sale_items si ON si.sale_id=s.id
        WHERE s.status='posted'
    """), {"month_start": month_start_dt, "prev_start": prev_start_dt, "prev_end": prev_end_dt, "tomorrow": tomorrow_start_dt}).mappings().one()

    expense_month = _money(db.execute(text("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date >= :s AND expense_date <= :e"), {"s": start_month, "e": today}).scalar_one())
    expense_prev = _money(db.execute(text("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date >= :s AND expense_date <= :e"), {"s": prev_month_start, "e": prev_month_end}).scalar_one())
    returns_month = _money(db.execute(text("SELECT COALESCE(SUM(total),0) FROM sales_returns WHERE status='posted' AND return_date >= :s AND return_date < :e"), {"s": month_start_dt, "e": tomorrow_start_dt}).scalar_one())

    low_stock = db.execute(text("""
        SELECT sku,name,current_stock,reorder_level,minimum_stock,buying_price,selling_price
        FROM products
        WHERE is_active=true AND item_type='product' AND track_inventory=true
          AND current_stock <= GREATEST(COALESCE(reorder_level,0),COALESCE(minimum_stock,0))
        ORDER BY current_stock ASC, name LIMIT 15
    """)).mappings().all()

    slow_movers = db.execute(text("""
        SELECT p.id,p.name,p.sku,p.current_stock,p.buying_price,COALESCE(SUM(si.quantity),0) AS sold_qty
        FROM products p
        LEFT JOIN sale_items si ON si.product_id=p.id
        LEFT JOIN sales s ON s.id=si.sale_id AND s.status='posted' AND s.sale_date >= :start_date
        WHERE p.is_active=true AND p.item_type='product' AND p.track_inventory=true AND p.current_stock > 0
        GROUP BY p.id,p.name,p.sku,p.current_stock,p.buying_price
        HAVING COALESCE(SUM(si.quantity),0)=0
        ORDER BY (p.current_stock*p.buying_price) DESC, p.name LIMIT 12
    """), {"start_date": month_start_dt - timedelta(days=30)}).mappings().all()

    top_products = db.execute(text("""
        SELECT p.name,COALESCE(SUM(si.line_total),0) sales,COALESCE(SUM(si.quantity),0) quantity,
               COALESCE(SUM(si.quantity*(si.unit_price-si.cost_price)),0) gross_profit
        FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id
        WHERE s.status='posted' AND s.sale_date >= :s AND s.sale_date < :e
        GROUP BY p.id,p.name ORDER BY sales DESC LIMIT 10
    """), {"s": month_start_dt, "e": tomorrow_start_dt}).mappings().all()

    top_customers = db.execute(text("""
        SELECT c.name,COALESCE(SUM(s.total),0) sales,COALESCE(SUM(s.paid),0) paid,
               COALESCE(SUM(s.total-s.paid),0) balance
        FROM customers c JOIN sales s ON s.customer_id=c.id
        WHERE s.status='posted' AND s.sale_date >= :s AND s.sale_date < :e
        GROUP BY c.id,c.name ORDER BY sales DESC LIMIT 10
    """), {"s": month_start_dt, "e": tomorrow_start_dt}).mappings().all()

    payment_mix = db.execute(text("""
        SELECT payment_method,COALESCE(SUM(amount),0) total FROM sale_payments sp
        JOIN sales s ON s.id=sp.sale_id
        WHERE s.status='posted' AND sp.created_at >= :s AND sp.created_at < :e
        GROUP BY payment_method ORDER BY total DESC
    """), {"s": month_start_dt, "e": tomorrow_start_dt}).mappings().all()

    inventory_value = _money(db.execute(text("SELECT COALESCE(SUM(current_stock*buying_price),0) FROM products WHERE is_active=true AND item_type='product' AND track_inventory=true")).scalar_one())
    inventory_units = _money(db.execute(text("SELECT COALESCE(SUM(current_stock),0) FROM products WHERE is_active=true AND item_type='product' AND track_inventory=true")).scalar_one())

    customer_aging = aging(db, "customer")
    customer_total = sum(Decimal(str(r.get("balance") or 0)) for r in customer_aging if Decimal(str(r.get("balance") or 0)) > 0)
    supplier_aging = aging(db, "supplier")
    supplier_total = sum(Decimal(str(r.get("balance") or 0)) for r in supplier_aging if Decimal(str(r.get("balance") or 0)) > 0)

    overdue_customers = db.execute(text("""
        SELECT c.name,c.credit_limit,COALESCE(SUM(s.total-s.paid),0) balance
        FROM customers c JOIN sales s ON s.customer_id=c.id
        WHERE c.is_active=true AND s.status='posted'
        GROUP BY c.id,c.name,c.credit_limit HAVING COALESCE(SUM(s.total-s.paid),0)>0
        ORDER BY balance DESC LIMIT 10
    """)).mappings().all()

    pending_approvals = int(db.execute(text("SELECT COUNT(*) FROM approval_requests WHERE status='pending'")).scalar_one())
    open_pos = int(db.execute(text("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('draft','sent','approved','partial')")).scalar_one())
    pending_deliveries = int(db.execute(text("SELECT COUNT(*) FROM delivery_orders WHERE status IN ('pending','dispatched')")).scalar_one())
    expiring_batches = int(db.execute(text("SELECT COUNT(*) FROM product_batches WHERE expiry_date IS NOT NULL AND expiry_date <= :d AND expiry_date >= :t"), {"d": today+timedelta(days=30), "t": today}).scalar_one())

    cash_balance = _money(db.execute(text("SELECT COALESCE(SUM(CASE WHEN transaction_type IN ('sale','customer_payment','cash_in','deposit') THEN amount ELSE -ABS(amount) END),0) FROM cash_transactions")).scalar_one())
    stock_transfer_pending = int(db.execute(text("SELECT COUNT(*) FROM stock_transfers WHERE status IN ('draft','requested','approved','dispatched')")).scalar_one())

    month_total = _money(sales_rows["month_total"])
    month_cost = _money(profit_rows["month_cost"])
    gross_profit = month_total - month_cost
    gross_margin = _pct(gross_profit, month_total)
    contribution = gross_profit - expense_month
    previous_mtd_total = _money(sales_rows["prev_mtd_total"])
    growth = _pct(month_total - previous_mtd_total, previous_mtd_total)
    aov = month_total / int(sales_rows["month_invoices"] or 1)

    shift = db.query(models.Shift).filter(models.Shift.user_id == user.id, models.Shift.status == "open").order_by(models.Shift.id.desc()).first()

    return {
        "business": {"name": getattr(profile,"business_name","CODEZONE Business"),"industry": industry,"currency": currency},
        "user": {"id": user.id,"role": role}, "page": page or "dashboard", "date": today.isoformat(),
        "sales": {"today": {"total": _money(sales_rows["today_total"]),"invoices": int(sales_rows["today_invoices"] or 0)},"week": {"total": _money(sales_rows["week_total"]),"invoices": int(sales_rows["week_invoices"] or 0)},"month": {"total": month_total,"invoices": int(sales_rows["month_invoices"] or 0),"paid": _money(sales_rows["month_paid"]),"discount": _money(sales_rows["month_discount"]),"subtotal": _money(sales_rows["month_subtotal"]),"avg_order": round(aov,2)},"previous_month": {"total": _money(sales_rows["prev_month_total"]),"expenses": expense_prev,"cost": _money(profit_rows["prev_cost"])},"previous_month_to_date": {"total": previous_mtd_total,"invoices": int(sales_rows["prev_mtd_invoices"] or 0)},"growth_pct": growth,"top_products": [dict(r) for r in top_products]},
        "profitability": {"month_cost": month_cost,"gross_profit": gross_profit,"gross_margin_pct": gross_margin,"expenses": expense_month,"contribution_after_expenses": contribution},
        "inventory": {"stock_value": inventory_value,"stock_units": inventory_units,"low_stock": [dict(r) for r in low_stock],"slow_movers": [dict(r) for r in slow_movers],"expiring_batches_30d": expiring_batches,"pending_transfers": stock_transfer_pending},
        "customers": {"receivables_total": _money(customer_total),"top_customers": [dict(r) for r in top_customers],"overdue_customers": [dict(r) for r in overdue_customers]},
        "suppliers": {"payables_total": _money(supplier_total),"open_purchase_orders": open_pos},
        "operations": {"pending_approvals": pending_approvals,"pending_deliveries": pending_deliveries,"cash_balance": cash_balance,"payment_mix": [dict(r) for r in payment_mix]},
        "shift": {"status": shift.status if shift else "closed","opening_cash": _money(shift.opening_cash) if shift else 0},
    }


def _minimal_business_context(db: Session, user: models.User, page: str | None = None) -> dict[str, Any]:
    """Safer, column-limited fallback when an optional optimized SQL snapshot fails.

    Query core fields only and isolate every optional section, so one missing table/column
    cannot turn the whole AI request into an unhandled HTTP 500. Missing sections are disclosed.
    """
    today, month_start, previous_start, previous_end, week_start, _ = _periods()
    tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time())
    month_dt = datetime.combine(month_start, datetime.min.time())
    prev_start_dt = datetime.combine(previous_start, datetime.min.time())
    prev_end_dt = datetime.combine(previous_end + timedelta(days=1), datetime.min.time())
    week_dt = datetime.combine(week_start, datetime.min.time())
    recent_dt = tomorrow - timedelta(days=30)
    warnings: list[str] = []

    def read(label: str, fn, default):
        try:
            return fn()
        except Exception:
            logger.exception("CODEZONE AI fallback read failed: %s", label)
            warnings.append(label)
            try:
                db.rollback()
            except Exception:
                pass
            return default

    profile = read("business_profile", lambda: db.query(models.BusinessProfile.business_name, models.BusinessProfile.currency, models.BusinessProfile.industry_profile).order_by(models.BusinessProfile.id).first(), None)
    role = read("user_role", lambda: db.query(models.Role.name).filter(models.Role.id == user.role_id).scalar() if user.role_id else None, None)

    # Query only stable sales columns and aggregate in Python so the fallback avoids raw-SQL drift.
    sale_rows = read("sales_core", lambda: db.query(models.Sale.id, models.Sale.sale_date, models.Sale.total, models.Sale.paid, models.Sale.status, models.Sale.customer_id).filter(models.Sale.status == "posted", models.Sale.sale_date >= prev_start_dt, models.Sale.sale_date < tomorrow).all(), [])
    sales = []
    for row in sale_rows:
        try:
            sales.append({"id": row.id, "sale_date": row.sale_date, "total": _money(row.total), "paid": _money(row.paid), "customer_id": row.customer_id})
        except Exception:
            warnings.append("sales_core_shape")

    def in_range(row, start, end):
        value = row.get("sale_date")
        return value is not None and start <= value < end
    today_start = datetime.combine(today, datetime.min.time())
    today_sales = [x for x in sales if in_range(x, today_start, tomorrow)]
    week_sales = [x for x in sales if in_range(x, week_dt, tomorrow)]
    month_sales = [x for x in sales if in_range(x, month_dt, tomorrow)]
    prev_sales = [x for x in sales if in_range(x, prev_start_dt, prev_end_dt)]
    month_total = sum(x["total"] for x in month_sales)
    today_total = sum(x["total"] for x in today_sales)
    week_total = sum(x["total"] for x in week_sales)
    prev_total = sum(x["total"] for x in prev_sales)
    month_paid = sum(x["paid"] for x in month_sales)

    expense_rows = read("expenses", lambda: db.query(models.Expense.amount, models.Expense.expense_date).filter(models.Expense.expense_date >= month_start, models.Expense.expense_date <= today).all(), [])
    expenses = sum(_money(x.amount) for x in expense_rows)

    product_rows = read("products_core", lambda: db.query(models.Product.id, models.Product.name, models.Product.sku, models.Product.current_stock, models.Product.reorder_level, models.Product.minimum_stock, models.Product.buying_price, models.Product.selling_price).filter(models.Product.is_active == True, models.Product.item_type == "product", models.Product.track_inventory == True).all(), [])
    products = []
    for row in product_rows:
        try:
            products.append({"id": row.id, "name": row.name, "sku": row.sku, "current_stock": _money(row.current_stock), "reorder_level": _money(row.reorder_level), "minimum_stock": _money(row.minimum_stock), "buying_price": _money(row.buying_price), "selling_price": _money(row.selling_price)})
        except Exception:
            warnings.append("products_core_shape")
    low_stock = [p for p in products if p["current_stock"] <= max(p["reorder_level"], p["minimum_stock"])]
    low_stock.sort(key=lambda p: (p["current_stock"], p["name"] or ""))

    # Details of sold products, used for sales velocity and reordering ranking.
    sale_ids_recent = [x["id"] for x in sales if x.get("sale_date") and x["sale_date"] >= recent_dt]
    item_rows = read("sale_items", lambda: db.query(models.SaleItem.sale_id, models.SaleItem.product_id, models.SaleItem.quantity, models.SaleItem.line_total).filter(models.SaleItem.sale_id.in_(sale_ids_recent)).all() if sale_ids_recent else [], [])
    sales_date_by_id = {x["id"]: x["sale_date"] for x in sales}
    product_by_id = {p["id"]: p for p in products}
    recent_sold: dict[int, float] = {}
    top_product_totals: dict[int, dict[str, float]] = {}
    for item in item_rows:
        try:
            pid=int(item.product_id); qty=_money(item.quantity); total=_money(item.line_total)
            if sales_date_by_id.get(item.sale_id) and sales_date_by_id[item.sale_id] >= recent_dt:
                recent_sold[pid] = recent_sold.get(pid, 0.0) + qty
            item_sale = next((x for x in sales if x["id"] == item.sale_id), None)
            if item_sale and item_sale["sale_date"] >= month_dt:
                rec=top_product_totals.setdefault(pid,{"sales":0.0,"quantity":0.0})
                rec["sales"] += total; rec["quantity"] += qty
        except Exception:
            warnings.append("sale_item_shape")
    top_products=[]
    for pid, vals in top_product_totals.items():
        p=product_by_id.get(pid,{}); top_products.append({"name":p.get("name") or f"Product {pid}","sales":vals["sales"],"quantity":vals["quantity"],"gross_profit":None})
    top_products.sort(key=lambda x:x["sales"],reverse=True)
    slow_movers=[{**p,"sold_qty":recent_sold.get(p["id"],0)} for p in products if p["current_stock"]>0 and recent_sold.get(p["id"],0)==0]
    slow_movers.sort(key=lambda x:x["current_stock"]*x["buying_price"],reverse=True)

    # Cost is optional in fallback; never pretend zero cost is verified if its query fails.
    month_sale_ids = [x["id"] for x in month_sales]
    cost_rows = read("cost_of_goods", lambda: db.query(models.SaleItem.sale_id, models.SaleItem.quantity, models.SaleItem.cost_price).filter(models.SaleItem.sale_id.in_(month_sale_ids)).all() if month_sale_ids else [], [])
    cost_by_sale={}
    if "cost_of_goods" not in warnings:
        for x in cost_rows:
            cost_by_sale[x.sale_id]=cost_by_sale.get(x.sale_id,0.0)+_money(x.quantity)*_money(x.cost_price)
    month_cost=sum(cost_by_sale.values())

    customers = read("customers", lambda: db.query(models.Customer.id, models.Customer.name, models.Customer.opening_balance).all(), [])
    customer_names={x.id:x.name for x in customers}
    opening_customer={x.id:_money(x.opening_balance) for x in customers}
    # Aggregate all receivables using compact field query; if unavailable, disclose that section.
    customer_balances=read("customer_balances", lambda: db.query(models.Sale.customer_id, func.coalesce(func.sum(models.Sale.total-models.Sale.paid),0)).filter(models.Sale.status=="posted",models.Sale.customer_id.is_not(None)).group_by(models.Sale.customer_id).all(), [])
    balances={int(cid):_money(balance)+opening_customer.get(int(cid),0) for cid,balance in customer_balances if cid is not None}
    customer_summary=[{"name":customer_names.get(cid,f"Customer {cid}"),"sales":0,"paid":0,"balance":bal} for cid,bal in balances.items() if bal>0]
    customer_summary.sort(key=lambda x:x["balance"],reverse=True)
    receivables=sum(x["balance"] for x in customer_summary)
    if "customer_balances" in warnings:
        receivables=sum(opening_customer.values())

    suppliers=read("suppliers", lambda: db.query(models.Supplier.id, models.Supplier.name, models.Supplier.opening_balance).all(), [])
    supplier_names={x.id:x.name for x in suppliers}; opening_supplier={x.id:_money(x.opening_balance) for x in suppliers}
    purchase_balances=read("supplier_balances", lambda: db.query(models.Purchase.supplier_id, func.coalesce(func.sum(models.Purchase.total-models.Purchase.paid),0)).filter(models.Purchase.status=="posted",models.Purchase.supplier_id.is_not(None)).group_by(models.Purchase.supplier_id).all(), [])
    supplier_summary=[{"name":supplier_names.get(int(sid),f"Supplier {sid}"),"balance":_money(balance)+opening_supplier.get(int(sid),0)} for sid,balance in purchase_balances if sid is not None]
    payables=sum(max(x["balance"],0) for x in supplier_summary)+sum(opening_supplier.values()) if "supplier_balances" in warnings else sum(max(x["balance"],0) for x in supplier_summary)

    def count_status(label, model, statuses):
        return read(label, lambda: int(db.query(model.id).filter(model.status.in_(statuses)).count()), 0)
    open_pos=count_status("purchase_orders",models.PurchaseOrder,["draft","sent","approved","partial"])
    pending_deliveries=count_status("delivery_orders",models.DeliveryOrder,["pending","dispatched"])
    pending_approvals=count_status("approvals",models.ApprovalRequest,["pending"])
    pending_transfers=count_status("stock_transfers",models.StockTransfer,["draft","requested","approved","dispatched"])
    expiring_batches=read("product_batches",lambda:int(db.query(models.ProductBatch.id).filter(models.ProductBatch.expiry_date.is_not(None),models.ProductBatch.expiry_date>=today,models.ProductBatch.expiry_date<=today+timedelta(days=30)).count()),0)
    cash_rows=read("cash_transactions",lambda:db.query(models.CashTransaction.amount,models.CashTransaction.transaction_type).all(),[])
    cash_balance=sum((_money(x.amount) if x.transaction_type in {"sale","customer_payment","cash_in","deposit"} else -abs(_money(x.amount)) for x in cash_rows),0.0)
    shift_row=read("active_shift",lambda:db.query(models.Shift.status,models.Shift.opening_cash).filter(models.Shift.user_id==user.id,models.Shift.status=="open").order_by(models.Shift.id.desc()).first(),None)

    top_customers=[]
    month_customer_totals={}
    for sale in month_sales:
        cid=sale.get("customer_id")
        if cid:
            rec=month_customer_totals.setdefault(cid,{"sales":0.0,"paid":0.0})
            rec["sales"]+=sale["total"];rec["paid"]+=sale["paid"]
    for cid,val in month_customer_totals.items():
        top_customers.append({"name":customer_names.get(cid,f"Customer {cid}"),"sales":val["sales"],"paid":val["paid"],"balance":max(val["sales"]-val["paid"],0)})
    top_customers.sort(key=lambda x:x["sales"],reverse=True)

    month_total_for_growth=month_total
    prev_mtd_end_date=previous_start+timedelta(days=min(today.day,(previous_end-previous_start).days+1))
    prev_mtd_end_dt=datetime.combine(prev_mtd_end_date,datetime.min.time())
    prev_mtd_sales=[x for x in sales if in_range(x,prev_start_dt,prev_mtd_end_dt)]
    prev_mtd_total=sum(x["total"] for x in prev_mtd_sales)
    business_name=getattr(profile,"business_name",None) if profile else None
    currency=getattr(profile,"currency",None) if profile else None
    industry=getattr(profile,"industry_profile",None) if profile else None
    return {
      "business":{"name":business_name or "CODEZONE Business","industry":industry or "business","currency":currency or "TZS"},
      "user":{"id":user.id,"role":role},"page":page or "dashboard","date":today.isoformat(),
      "sales":{"today":{"total":today_total,"invoices":len(today_sales)},"week":{"total":week_total,"invoices":len(week_sales)},"month":{"total":month_total,"invoices":len(month_sales),"paid":month_paid,"discount":0,"subtotal":month_total,"avg_order":round(month_total/max(len(month_sales),1),2)},"previous_month":{"total":prev_total,"expenses":0,"cost":0},"previous_month_to_date":{"total":prev_mtd_total,"invoices":len(prev_mtd_sales)},"growth_pct":_pct(month_total-prev_mtd_total,prev_mtd_total),"top_products":top_products[:10]},
      "profitability":{"month_cost":month_cost,"gross_profit":month_total-month_cost,"gross_margin_pct":_pct(month_total-month_cost,month_total),"expenses":expenses,"contribution_after_expenses":month_total-month_cost-expenses},
      "inventory":{"stock_value":sum(p["current_stock"]*p["buying_price"] for p in products),"stock_units":sum(p["current_stock"] for p in products),"low_stock":low_stock[:15],"slow_movers":slow_movers[:12],"expiring_batches_30d":expiring_batches,"pending_transfers":pending_transfers},
      "customers":{"receivables_total":receivables,"top_customers":top_customers[:10],"overdue_customers":customer_summary[:10]},
      "suppliers":{"payables_total":payables,"open_purchase_orders":open_pos},
      "operations":{"pending_approvals":pending_approvals,"pending_deliveries":pending_deliveries,"cash_balance":cash_balance,"payment_mix":[]},
      "shift":{"status":shift_row.status if shift_row else "closed","opening_cash":_money(shift_row.opening_cash) if shift_row else 0},
      "data_quality":{"partial":bool(warnings),"unavailable_sections":sorted(set(warnings)),"notice":"Fallback snapshot: some operational figures may be incomplete. Verify missing sections before decisions." if warnings else None}
    }


def _business_context_safe(db: Session, user: models.User, page: str | None = None, lang: str = "en") -> tuple[dict[str, Any], bool]:
    """Use full verified context if possible; fallback with explicit warning rather than HTTP 500."""
    try:
        context = _business_context(db, user, page)
        with _AI_STATE_LOCK:
            _AI_STATE["data_context_warning"] = None
        return context, False
    except Exception:
        logger.exception("CODEZONE AI rich context snapshot failed; switching to column-limited fallback")
        try:
            db.rollback()
        except Exception:
            pass
    try:
        context = _minimal_business_context(db, user, page)
        quality = context.setdefault("data_quality", {})
        unavailable = list(quality.get("unavailable_sections", []))
        if "detailed_sql_snapshot" not in unavailable:
            unavailable.append("detailed_sql_snapshot")
        quality["partial"] = True
        quality["unavailable_sections"] = sorted(set(unavailable))
        base_notice = str(quality.get("notice") or "").strip()
        fallback_notice = "A fallback snapshot was used; some detailed metrics may be incomplete. Verify figures before making decisions."
        quality["notice"] = (base_notice + " " + fallback_notice).strip() if base_notice and fallback_notice not in base_notice else (base_notice or fallback_notice)
        warning = "full_snapshot_failed:" + ",".join(unavailable[:8])
        with _AI_STATE_LOCK:
            _AI_STATE["data_context_warning"] = warning
        return context, True
    except Exception as exc:
        logger.exception("CODEZONE AI fallback snapshot also failed")
        with _AI_STATE_LOCK:
            _AI_STATE["data_context_warning"] = "business_database_read_failed"
        detail = "AI haikuweza kusoma data za biashara. Angalia error log ya PowerShell; hakuna namba iliyobuniwa." if lang == "sw" else "AI could not read business data. Check the CODEZONE PowerShell error log; no figures were invented."
        raise HTTPException(status_code=503, detail=detail) from exc


def _advice_playbook(ctx: dict[str, Any], lang: str) -> dict[str, Any]:
    c = ctx["business"]["currency"]
    sales = ctx["sales"]; prof=ctx["profitability"]; inv=ctx["inventory"]; cust=ctx["customers"]; ops=ctx["operations"]; sup=ctx["suppliers"]
    risks=[]; actions=[]; evidence=[]

    if sales["growth_pct"] < -5:
        risks.append(("high", "Mauzo ya mwezi hadi leo yameshuka kwa %.1f%% dhidi ya siku zinazolingana mwezi uliopita." % sales["growth_pct"], "Month-to-date sales are down by %.1f%% versus the same period last month." % sales["growth_pct"]))
        actions.append(("high", "Chunguza bidhaa/wateja waliopunguza volume, kisha tengeneza mpango wa sales wa siku 14." if lang=="sw" else "Review the products/customers driving the decline, then create a 14-day sales recovery plan."))
    elif sales["growth_pct"] > 10:
        actions.append(("medium", "Linda ukuaji huu kwa kuhakikisha stock, cash na margin vinaweza ku-support volume mpya." if lang=="sw" else "Protect this growth by checking that inventory, cash and margin can support the higher volume."))

    if prof["gross_margin_pct"] < 15 and sales["month"]["total"] > 0:
        risks.append(("high", "Gross margin iko chini ya 15%%." if lang=="sw" else "Gross margin is below 15%."))
        actions.append(("high", "Pitia cost price, discounts na products zenye margin ndogo kabla ya kuongeza volume." if lang=="sw" else "Review cost prices, discounts and low-margin products before chasing more volume."))
    elif prof["gross_margin_pct"] >= 25:
        actions.append(("medium", "Margin inaonekana nzuri; sasa linda margin kwa kudhibiti discounts na cost drift." if lang=="sw" else "Margin looks healthy; protect it by controlling discounts and cost drift."))

    if inv["low_stock"]:
        risks.append(("high", f"Kuna bidhaa {len(inv['low_stock'])} zilizo chini ya reorder/minimum level." if lang=="sw" else f"{len(inv['low_stock'])} items are at or below reorder/minimum level."))
        actions.append(("high", "Panga reorder kwa kuanza na bidhaa zenye sales velocity kubwa na cash impact ndogo." if lang=="sw" else "Prioritize reorders by sales velocity and cash impact rather than simply ordering everything at once."))
    if inv["slow_movers"]:
        tied = sum(_money(x["current_stock"])*_money(x["buying_price"]) for x in inv["slow_movers"])
        risks.append(("medium", f"Bidhaa slow-moving zimefungia takriban {c} {tied:,.0f} ya stock value." if lang=="sw" else f"Slow-moving products tie up about {c} {tied:,.0f} of inventory value."))
        actions.append(("medium", "Tengeneza clearance/discount ya kimkakati au simamisha reorder ya slow movers." if lang=="sw" else "Create a targeted clearance/discount plan or pause reordering of slow movers."))

    if cust["receivables_total"] > 0:
        risks.append(("medium", f"Customer receivables ni {c} {cust['receivables_total']:,.0f}." if lang=="sw" else f"Customer receivables are {c} {cust['receivables_total']:,.0f}."))
        actions.append(("high", "Weka collection priority kwa balances kubwa na customers waliovuka payment terms." if lang=="sw" else "Prioritize collections for the largest balances and customers beyond their payment terms."))

    if prof["expenses"] > 0 and prof["gross_profit"] > 0 and prof["expenses"] > prof["gross_profit"]*0.6:
        risks.append(("high", "Operating expenses zinatumia zaidi ya 60%% ya gross profit." if lang=="sw" else "Operating expenses consume more than 60% of gross profit."))
        actions.append(("high", "Tenganisha fixed vs variable expenses na kata matumizi yasiyoongeza sales au uwezo wa delivery." if lang=="sw" else "Separate fixed from variable costs and reduce spending that does not improve sales or delivery capacity."))

    if ops["pending_approvals"]:
        actions.append(("medium", f"Funga approvals {ops['pending_approvals']} zilizo pending kabla ya mwisho wa siku." if lang=="sw" else f"Clear the {ops['pending_approvals']} pending approvals before end of day."))
    if ops["pending_deliveries"]:
        actions.append(("medium", f"Kagua deliveries {ops['pending_deliveries']} zilizo pending/dispatched." if lang=="sw" else f"Review the {ops['pending_deliveries']} pending/dispatched deliveries."))

    evidence.extend([
        f"Sales month: {c} {sales['month']['total']:,.0f}",
        f"Gross margin: {prof['gross_margin_pct']:.1f}%",
        f"Receivables: {c} {cust['receivables_total']:,.0f}",
        f"Stock value: {c} {inv['stock_value']:,.0f}",
    ])

    if lang == "sw":
        summary = f"Hii ni business snapshot ya {ctx['business']['name']}. Mauzo ya mwezi ni {c} {sales['month']['total']:,.0f}, gross margin ni {prof['gross_margin_pct']:.1f}%, na contribution baada ya expenses ni {c} {prof['contribution_after_expenses']:,.0f}."
    else:
        summary = f"This is a business snapshot for {ctx['business']['name']}. Monthly sales are {c} {sales['month']['total']:,.0f}, gross margin is {prof['gross_margin_pct']:.1f}%, and contribution after expenses is {c} {prof['contribution_after_expenses']:,.0f}."

    return {"summary": summary,"risks": risks[:6],"actions": actions[:7],"evidence": evidence}


def _has_any(q: str, terms: tuple[str, ...] | list[str]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)", q) for term in terms)


def _local_answer(message: str, ctx: dict[str, Any], lang: str) -> dict[str, Any]:
    """Useful offline business advice grounded only in the verified system snapshot."""
    q = re.sub(r"\s+", " ", message.lower()).strip()
    c = ctx["business"]["currency"]
    sales = ctx["sales"]
    prof = ctx["profitability"]
    inv = ctx["inventory"]
    cust = ctx["customers"]
    ops = ctx["operations"]
    sup = ctx["suppliers"]
    play = _advice_playbook(ctx, lang)

    sw = lang == "sw"
    suggestions = ([("Nipe mpango wa kuongeza mauzo" if sw else "Build a sales growth plan"),
                   ("Nionyeshe bidhaa zinazohitaji kuagizwa" if sw else "Show products that need reordering"),
                   ("Nisaidie kuboresha cash flow" if sw else "Help me improve cash flow"),
                   ("Chambua faida na gharama" if sw else "Analyze profit and expenses")])

    if _has_any(q, ("habari", "mambo", "hello", "hi", "good morning", "good afternoon", "asante", "thanks", "wewe ni nani", "who are you")) and len(q.split()) <= 7:
        answer = ("Habari! Mimi ni CODEZONE AI Advisor. Naweza kusaidia kuchambua mauzo, faida, gharama, stock, madeni, manunuzi na hatua za kuboresha biashara. Naweza kutumia data iliyo kwenye CODEZONE; niambie unataka kuchunguza eneo gani." if sw else
                  "Hello! I’m CODEZONE AI Advisor. I can help analyze sales, profit, expenses, inventory, receivables, purchasing and practical business improvements using the data available in CODEZONE. What would you like to investigate?")
        return {"answer": answer, "source": "CODEZONE AI · Local Advisor", "suggestions": suggestions}

    if _has_any(q, ("what can you do", "unaweza kufanya nini", "unaweza kunisaidia nini", "msaada", "help", "capabilities", "how do i use")):
        answer = ("Naweza: (1) kuchambua mauzo na mwenendo wake, (2) kukokotoa gross profit/margin kwa snapshot ya sasa, (3) kuonyesha hatari za stock, (4) kupitia madeni na cash flow, (5) kupanga vipaumbele vya manunuzi na (6) kupendekeza mpango wa siku 7–14. Nikikosa data ya kuthibitisha jambo, nitakuambia badala ya kubuni namba." if sw else
                  "I can (1) analyze sales trends, (2) explain gross profit/margin from the current snapshot, (3) identify inventory risks, (4) review receivables and cash flow, (5) prioritize purchasing, and (6) propose a 7–14 day action plan. If verified data is missing, I’ll say so rather than invent numbers.")
        return {"answer": answer, "source": "CODEZONE AI · Local Advisor", "suggestions": suggestions}

    if _has_any(q, ("top product", "top products", "top selling products", "best selling", "best-selling", "which products sell best", "which product sells best", "products sell best", "bidhaa zinazouza", "bidhaa zinazo uza", "bidhaa zinazouzwa", "bidhaa bora", "bidhaa gani inauza", "bidhaa gani zinauza")):
        rows = sales["top_products"]
        if rows:
            top = rows[:5]
            lines = [f"{i+1}. {r['name']} — {c} {_money(r['sales']):,.0f} sales; quantity {_money(r['quantity']):,.2f}" for i, r in enumerate(top)]
            answer = ("Bidhaa zilizoongoza kwa mauzo mwezi huu, kwa rekodi zilizopo: \n" if sw else "Top products by sales this month, from recorded transactions:\n") + "\n".join(lines)
            answer += ("\n\nHatua: hakiki margin ya kila bidhaa, stock iliyobaki na kama mauzo yamejikita kwa bidhaa chache." if sw else "\n\nNext: check each product’s margin, remaining stock and whether sales are concentrated in only a few products.")
        else:
            answer = ("Sijaona mauzo yaliyorekodiwa kwa bidhaa katika kipindi cha mwezi huu, hivyo siwezi kutaja bidhaa zinazoongoza kwa ushahidi." if sw else "I found no recorded product sales for the current month, so I can’t reliably rank best-selling products yet.")
        evidence = [f"Products ranked: {len(rows)}", f"Period: {ctx['date'][:7]}"]
        return {"answer": answer, "source": "CODEZONE AI · Product sales analysis", "evidence": evidence, "suggestions": suggestions}

    if _has_any(q, ("cash flow", "cashflow", "cash", "fedha", "mtiririko wa fedha", "pesa iliyopo", "salio la fedha", "bank balance")):
        answer = (f"Salio la cash ledger lililohesabiwa ni {c} {ops['cash_balance']:,.0f}. Mauzo ya mwezi ni {c} {sales['month']['total']:,.0f}; kiasi kilicholipwa kwenye sales hizo ni {c} {sales['month']['paid']:,.0f}; madeni ya wateja ni {c} {cust['receivables_total']:,.0f}; madeni ya suppliers ni {c} {sup['payables_total']:,.0f}." if sw else
                  f"The calculated cash-ledger balance is {c} {ops['cash_balance']:,.0f}. Monthly sales are {c} {sales['month']['total']:,.0f}; paid amount on those sales is {c} {sales['month']['paid']:,.0f}; customer receivables are {c} {cust['receivables_total']:,.0f}; supplier payables are {c} {sup['payables_total']:,.0f}.")
        answer += ("\n\nTahadhari: salio la ledger si lazima liwe sawa na fedha halisi ya benki/kasha mpaka transactions zote ziwe zimeingizwa na reconciled. Kwanza linganisha salio na cash count/bank statement, kisha panga collection na malipo ya karibu." if sw else "\n\nCaution: a ledger balance is not necessarily equal to physical cash or the bank balance until transactions are complete and reconciled. Match it to a cash count/bank statement, then plan collections and near-term payments.")
        evidence = [f"Cash ledger: {c} {ops['cash_balance']:,.0f}", f"Receivables: {c} {cust['receivables_total']:,.0f}", f"Payables: {c} {sup['payables_total']:,.0f}"]
        actions = (["Reconcile cash and bank balances", "Rank overdue receivables", "List supplier payments due next"])
        return {"answer": answer, "source": "CODEZONE AI · Cash-flow analysis", "evidence": evidence, "actions": actions, "suggestions": suggestions}

    if _has_any(q, ("debt", "madeni", "deni", "receivable", "receivables", "customer balance", "credit", "wateja wanaodaiwa", "nani anadaiwa", "customers owe", "overdue")):
        debt_rows = cust.get("overdue_customers", [])
        answer = (f"Madeni ya wateja yaliyohesabiwa ni {c} {cust['receivables_total']:,.0f}." if sw else f"Calculated customer receivables total {c} {cust['receivables_total']:,.0f}.")
        if debt_rows:
            answer += ("\nWateja wenye balance kubwa kulingana na rekodi:" if sw else "\nCustomers with the largest recorded outstanding balances:") + "\n" + "\n".join(f"• {r['name']}: {c} {_money(r['balance']):,.0f}" for r in debt_rows[:5])
        answer += ("\n\nHatua: hakiki tarehe za invoice na malipo kwanza, wasiliana na wateja kwa mpango wa malipo, na usiongeze credit bila kuangalia credit limit." if sw else "\n\nNext: verify invoice and payment dates, contact customers with a payment plan, and avoid extending more credit without checking credit limits.")
        return {"answer": answer, "source": "CODEZONE AI · Receivables analysis", "evidence": [f"Receivables: {c} {cust['receivables_total']:,.0f}", f"Customers in ranked list: {len(debt_rows)}"], "suggestions": suggestions}

    if _has_any(q, ("stock", "inventory", "bidhaa", "stoo", "reorder", "kuagiza", "uagizaji", "low stock", "stock out", "kuisha", "slow mover", "slow-moving", "expire", "expiry", "zinaisha", "muda wa bidhaa")):
        low = inv["low_stock"][:8]
        slow = inv["slow_movers"][:5]
        answer = (f"Kuna bidhaa {len(inv['low_stock'])} zilizo kwenye/chini ya reorder au minimum level; stock value ya bidhaa zinazofuatiliwa ni {c} {inv['stock_value']:,.0f}." if sw else f"There are {len(inv['low_stock'])} products at/below their reorder or minimum level; tracked inventory value is {c} {inv['stock_value']:,.0f}.")
        if low:
            answer += ("\nBidhaa za kuangalia kwanza:" if sw else "\nItems to check first:") + "\n" + "\n".join(f"• {x['name']} — stock {x['current_stock']}, reorder {x.get('reorder_level', 0)}" for x in low)
        if slow:
            answer += (f"\nBidhaa {len(inv['slow_movers'])} zina stock lakini hazina mauzo yaliyorekodiwa ndani ya dirisha la hivi karibuni." if sw else f"\n{len(inv['slow_movers'])} items have stock but no sales recorded in the recent lookback window.")
        answer += ("\n\nUsiagize kila bidhaa moja kwa moja. Hakiki mauzo ya siku 30–60, lead time ya supplier, expiry, stock iliyo kwenye branches nyingine na cash iliyopo kabla ya kuamua kiasi." if sw else "\n\nDo not reorder everything automatically. Check 30–60 day sales velocity, supplier lead time, expiry, stock at other branches, and available cash before setting quantities.")
        return {"answer": answer, "source": "CODEZONE AI · Inventory analysis", "evidence": [f"Low-stock items: {len(inv['low_stock'])}", f"Slow-moving items: {len(inv['slow_movers'])}", f"Tracked stock value: {c} {inv['stock_value']:,.0f}"], "actions": ["Review low-stock list", "Check sales velocity and lead times", "Pause reorders for inactive products"], "suggestions": suggestions}

    if _has_any(q, ("expense", "expenses", "gharama", "matumizi", "cost", "overhead", "spending", " matumizi ")):
        ratio = (prof["expenses"] / prof["gross_profit"] * 100) if prof["gross_profit"] > 0 else None
        answer = (f"Gharama zilizoingizwa mwezi huu ni {c} {prof['expenses']:,.0f}; gross profit iliyokokotolewa ni {c} {prof['gross_profit']:,.0f}; contribution baada ya gharama hizi ni {c} {prof['contribution_after_expenses']:,.0f}." if sw else f"Expenses recorded this month are {c} {prof['expenses']:,.0f}; calculated gross profit is {c} {prof['gross_profit']:,.0f}; contribution after these expenses is {c} {prof['contribution_after_expenses']:,.0f}.")
        if ratio is not None:
            answer += (f" Gharama ni {ratio:.1f}% ya gross profit." if sw else f" Expenses equal {ratio:.1f}% of gross profit.")
        answer += ("\n\nPanga matumizi kwa fixed/variable na muhimu/isiyo ya lazima; kata au renegotiate kwanza matumizi yasiyoathiri ubora, compliance au huduma kwa mteja. Hakikisha expenses zote za kipindi zimeingizwa kabla ya kuhitimisha." if sw else "\n\nGroup expenses into fixed/variable and essential/discretionary; reduce or renegotiate costs that do not harm quality, compliance or customer service. Confirm all period expenses are recorded before drawing conclusions.")
        return {"answer": answer, "source": "CODEZONE AI · Expense analysis", "evidence": [f"Expenses: {c} {prof['expenses']:,.0f}", f"Gross profit: {c} {prof['gross_profit']:,.0f}", f"Contribution: {c} {prof['contribution_after_expenses']:,.0f}"], "suggestions": suggestions}

    if _has_any(q, ("profit", "faida", "gross margin", "margin", "hasara", "loss", "profitability", "margins")):
        gp = prof["gross_profit"]
        answer = (f"Kwa snapshot ya mwezi huu, sales ni {c} {sales['month']['total']:,.0f}, cost of goods sold iliyokokotolewa ni {c} {prof['month_cost']:,.0f}, gross profit ni {c} {gp:,.0f}, na gross margin ni {prof['gross_margin_pct']:.1f}%. Expenses zilizoingizwa ni {c} {prof['expenses']:,.0f}, hivyo contribution baada ya expenses ni {c} {prof['contribution_after_expenses']:,.0f}." if sw else f"For the current monthly snapshot, sales are {c} {sales['month']['total']:,.0f}, calculated cost of goods sold is {c} {prof['month_cost']:,.0f}, gross profit is {c} {gp:,.0f}, and gross margin is {prof['gross_margin_pct']:.1f}%. Recorded expenses are {c} {prof['expenses']:,.0f}, leaving contribution after expenses of {c} {prof['contribution_after_expenses']:,.0f}.")
        answer += ("\n\nHatua: anza na bidhaa zenye volume kubwa lakini margin ndogo, discounts/returns, na mabadiliko ya buying cost. Hii ni contribution ya snapshot, si audited net profit ikiwa kodi, depreciation, interest au gharama nyingine hazipo kwenye mfumo." if sw else "\n\nNext: start with high-volume/low-margin products, discounts/returns, and changes in buying costs. This is snapshot contribution, not audited net profit if tax, depreciation, interest or other expenses are not recorded here.")
        return {"answer": answer, "source": "CODEZONE AI · Profitability analysis", "evidence": [f"Sales: {c} {sales['month']['total']:,.0f}", f"COGS estimate: {c} {prof['month_cost']:,.0f}", f"Gross margin: {prof['gross_margin_pct']:.1f}%", f"Expenses: {c} {prof['expenses']:,.0f}"], "actions": ["Review low-margin items", "Check discount and return patterns", "Validate product cost prices"], "suggestions": suggestions}

    if _has_any(q, ("sales", "mauzo", "revenue", "mapato", "turnover", "invoices", "orders sold", "mauzo ya leo", "mauzo ya mwezi")):
        change = sales["growth_pct"]
        direction = "yameongezeka" if change >= 0 else "yameshuka"
        direction_en = "increased" if change >= 0 else "declined"
        answer = (f"Mauzo ya leo ni {c} {sales['today']['total']:,.0f} kwenye invoices {sales['today']['invoices']}; ya wiki hii ni {c} {sales['week']['total']:,.0f}; na ya mwezi huu ni {c} {sales['month']['total']:,.0f} kupitia invoices {sales['month']['invoices']}. Mauzo ya mwezi hadi leo {direction} kwa {abs(change):.1f}% dhidi ya siku zinazolingana mwezi uliopita. Average order value ni {c} {sales['month']['avg_order']:,.0f}." if sw else f"Today's sales are {c} {sales['today']['total']:,.0f} across {sales['today']['invoices']} invoices; this week's sales are {c} {sales['week']['total']:,.0f}; this month's sales are {c} {sales['month']['total']:,.0f} across {sales['month']['invoices']} invoices. Month-to-date sales {direction_en} by {abs(change):.1f}% versus the same period last month. Average order value is {c} {sales['month']['avg_order']:,.0f}.")
        answer += ("\n\nChunguza tofauti kwa bidhaa, wateja, discount na stockouts. Hakikisha mwezi uliopita umefungwa/kuingizwa kikamilifu kabla ya kutafsiri growth." if sw else "\n\nInvestigate product/customer mix, discounts and stockouts. Ensure the previous month is fully recorded before interpreting growth.")
        return {"answer": answer, "source": "CODEZONE AI · Sales analysis", "evidence": [f"Today: {c} {sales['today']['total']:,.0f}", f"Month: {c} {sales['month']['total']:,.0f}", f"Month growth: {change:.1f}%", f"Average order: {c} {sales['month']['avg_order']:,.0f}"], "suggestions": suggestions}

    if _has_any(q, ("purchase", "purchases", "supplier", "suppliers", "manunuzi", "wasambazaji", "agiza", "kuagiza", "purchase order", "po")):
        answer = (f"CODEZONE inaonyesha supplier payables za {c} {sup['payables_total']:,.0f} na purchase orders {sup['open_purchase_orders']} zilizo wazi." if sw else f"CODEZONE shows supplier payables of {c} {sup['payables_total']:,.0f} and {sup['open_purchase_orders']} open purchase orders.")
        answer += ("\n\nKabla ya kuunda order mpya, linganisha stock iliyopo, sales velocity, order ambazo hazijapokelewa, lead time, na tarehe za kulipa supplier. Namba hizi haziwakilishi moja kwa moja stockout forecast ikiwa lead times na reorder quantities hazijajazwa." if sw else "\n\nBefore placing a new order, compare current stock, sales velocity, unreceived orders, lead times and supplier due dates. These counts are not a full stockout forecast if lead times and reorder quantities have not been configured.")
        return {"answer": answer, "source": "CODEZONE AI · Purchasing analysis", "evidence": [f"Open purchase orders: {sup['open_purchase_orders']}", f"Supplier payables: {c} {sup['payables_total']:,.0f}"], "suggestions": suggestions}

    if _has_any(q, ("risk", "risks", "hatari", "muhtasari", "summary", "pulse", "status", "biashara yangu", "my business", "business health", "ikoje", "how is business")):
        risks = play["risks"]
        actions = play["actions"]
        answer = play["summary"]
        if risks:
            answer += "\n\n" + ("Vipaumbele vya hatari:" if sw else "Priority risks:") + "\n" + "\n".join(f"• {(sw_text if sw else en_text)}" for _, sw_text, en_text in risks[:5])
        else:
            answer += "\n\n" + ("Hakuna red flag kubwa iliyopatikana kwenye metrics hizi, lakini thibitisha completeness ya data." if sw else "\n\nNo major red flag was detected in these metrics, but verify the completeness of the underlying data.")
        if actions:
            answer += "\n\n" + ("Hatua za kuchukua:" if sw else "Recommended next steps:") + "\n" + "\n".join(f"{i+1}. {a}" for i, (_, a) in enumerate(actions[:5]))
        return {"answer": answer, "source": "CODEZONE AI · Business Pulse", "evidence": play["evidence"], "actions": [a for _, a in actions[:5]], "suggestions": suggestions}

    # Default local mode cannot reason like an LLM, so it should be candid and guide users to
    # supported questions rather than pretend to understand an unrelated or underspecified query.
    fallback = ("Naweza kusaidia kwa data ya biashara ya CODEZONE, lakini sijatambua vizuri unachotaka kwenye swali hili. Taja mojawapo: mauzo, faida/margin, gharama, stock, bidhaa zinazoongoza, madeni ya wateja, cash flow au manunuzi. Ukiuliza kuhusu namba ya rekodi fulani, nipe jina/kipindi ili niweze kujibu kwa data iliyopo." if sw else
                "I can answer questions grounded in CODEZONE business data, but I could not confidently identify the intent of that question in local mode. Try sales, profit/margin, expenses, stock, best-selling products, customer receivables, cash flow or purchasing. For a specific record or period, include its name/date so I can answer from the available data.")
    return {"answer": fallback, "source": "CODEZONE AI · Local Advisor (limited mode)", "mode": "local", "suggestions": suggestions}


def _llm_answer(message: str, context: dict[str, Any], lang: str, history: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
    cfg = _provider_config()
    if not cfg["configured"]:
        return None
    language_name = "Kiswahili" if lang == "sw" else "English"
    system = f"""You are CODEZONE AI Advisor, an expert small-business operator, finance-aware analyst, inventory planner, sales coach and ERP assistant embedded inside CODEZONE.

LANGUAGE: Respond in {language_name}. If the user explicitly asks for another language, follow that request.

CORE BEHAVIOR:
- Understand everyday Kiswahili, Tanzanian business Swanglish, and English. Ask one concise follow-up if the request is genuinely ambiguous.
- Think like a practical business advisor, not a generic chatbot.
- Use the verified business snapshot below as the source of truth for numeric facts. If data_quality.partial is true, disclose that the snapshot is incomplete and do not present fallback zeroes as verified totals.
- Explain what is happening, why it matters, what the user should do next, and what outcome to expect.
- Distinguish facts from inference and recommendations. Never invent missing numbers or claim database access beyond the supplied snapshot.
- Be commercially useful: prioritize profit, cash flow, stock availability, customer collections, supplier commitments and operational control.
- When a calculation can be derived from supplied facts, show the calculation briefly; otherwise state what data is missing.
- Use compact sections and actionable advice. Avoid repeating the entire snapshot when the user asks a narrow question.
- Never expose secrets, credentials, raw database URLs or hidden system details.
- Never claim an action was executed unless a CODEZONE tool confirms it. Do not execute write actions.
- Recommend low-risk, reversible steps first.

BUSINESS KNOWLEDGE:
{json.dumps(KNOWLEDGE, ensure_ascii=False)}
"""
    messages = [{"role": "system", "content": system}]
    # The frontend includes the current user message in history; remove that duplicate because
    # the current question is appended below alongside its verified business snapshot.
    clean_history: list[dict[str, str]] = []
    if history:
        for item in history[-10:]:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"} or not item.get("content"):
                continue
            content = str(item["content"])[:4000]
            clean_history.append({"role": item["role"], "content": content})
        if clean_history and clean_history[-1]["role"] == "user" and clean_history[-1]["content"].strip() == message.strip():
            clean_history.pop()
    messages.extend(clean_history[-8:])
    messages.append({"role": "user", "content": json.dumps({"question": message, "verified_business_context": context}, ensure_ascii=False, default=str)})
    payload = {"model": cfg["model"], "messages": messages}
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as response:
            body = json.loads(response.read().decode("utf-8"))
        choices = body.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if isinstance(content, list):
            content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Provider returned an empty answer")
        with _AI_STATE_LOCK:
            _AI_STATE.update({"remote_available": True, "last_error": None, "last_failure_at": None})
        return {"answer": content.strip(), "source": f"CODEZONE AI · {cfg['model']}", "mode": "remote",
                "suggestions": ["Give me a 7-day action plan", "How can I improve profit?", "What is my biggest business risk?", "What should I reorder first?"]}
    except urllib.error.HTTPError as exc:
        with _AI_STATE_LOCK:
            error_label = _http_error_label(exc.code)
            _AI_STATE.update({"remote_available": False, "last_error": error_label, "last_failure_at": datetime.utcnow().isoformat() + "Z"})
        logger.warning("CODEZONE AI remote provider returned HTTP %s (%s)", exc.code, error_label)
        return None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        reason = "timeout" if isinstance(exc, TimeoutError) else "connection error" if isinstance(exc, urllib.error.URLError) else "invalid provider response"
        with _AI_STATE_LOCK:
            _AI_STATE.update({"remote_available": False, "last_error": reason, "last_failure_at": datetime.utcnow().isoformat() + "Z"})
        logger.warning("CODEZONE AI remote provider failed (%s): %s", reason, type(exc).__name__)
        return None


AI_CONFIG_KEYS = {
    "CODEZONE_AI_PROVIDER", "CODEZONE_AI_MODEL", "CODEZONE_AI_API_KEY",
    "CODEZONE_AI_BASE_URL", "CODEZONE_AI_TIMEOUT_SECONDS",
}


def _http_error_label(code: int) -> str:
    labels = {
        400: "HTTP 400 · check model name and provider settings",
        401: "HTTP 401 · API key was rejected",
        403: "HTTP 403 · provider/model access denied",
        404: "HTTP 404 · model or API endpoint not found",
        408: "HTTP 408 · provider timed out",
        429: "HTTP 429 · rate limit or API quota/billing problem",
        500: "HTTP 500 · provider had an internal error",
        502: "HTTP 502 · provider gateway error",
        503: "HTTP 503 · provider temporarily unavailable",
    }
    return labels.get(int(code), f"HTTP {int(code)} · provider request failed")


def _ai_config_file_path() -> Path:
    explicit = os.environ.get("CODEZONE_CONFIG_FILE")
    if explicit:
        return Path(explicit)
    program_data = Path(os.environ.get("PROGRAMDATA", str(Path.home())))
    program_file = program_data / "CODEZONE" / "config.env"
    if program_file.exists() or os.environ.get("PROGRAMDATA"):
        return program_file
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    project_env = Path(__file__).resolve().parents[3] / ".env"
    if project_env.exists():
        return project_env
    return cwd_env


def _write_ai_config(values: dict[str, str], clear_key: bool = False) -> Path:
    """Update only AI settings in the existing config file; preserve every unrelated secret."""
    path = _ai_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = original.splitlines()
    wanted = dict(values)
    if clear_key:
        wanted["CODEZONE_AI_API_KEY"] = ""
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key not in AI_CONFIG_KEYS:
            output.append(line)
            continue
        if key in seen:
            continue
        seen.add(key)
        value = wanted.get(key)
        if value is None:
            output.append(line)
        elif value == "" and key == "CODEZONE_AI_API_KEY":
            # An empty API key is equivalent to clearing it, so don't persist a blank secret.
            continue
        else:
            output.append(f"{key}={value}")
    for key, value in wanted.items():
        if key in seen:
            continue
        if value == "" and key == "CODEZONE_AI_API_KEY":
            continue
        output.append(f"{key}={value}")
    contents = "\n".join(output).rstrip() + "\n"
    tmp = path.with_name(path.name + ".ai-tmp")
    tmp.write_text(contents, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        # On Windows, file access is controlled by the containing directory ACL.
        pass
    return path


def _normalize_ai_settings(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or _provider_config()
    provider = str(payload.get("provider") or existing.get("provider") or "local").strip().lower()
    if provider not in {"openai", "ollama", "local"}:
        raise HTTPException(400, "Provider must be OpenAI, Ollama or Local advisor.")
    model = str(payload.get("model") or ("qwen2.5:7b" if provider == "ollama" else "gpt-5.6-terra")).strip()
    if provider != "local" and (not model or len(model) > 120 or any(ch.isspace() for ch in model)):
        raise HTTPException(400, "Enter a valid model ID without spaces.")
    default_url = "http://127.0.0.1:11434/v1" if provider == "ollama" else "https://api.openai.com/v1"
    base_url = str(payload.get("base_url") or default_url).strip().rstrip("/")
    if "\n" in base_url or "\r" in base_url:
        raise HTTPException(400, "Provider base URL must be a single-line URL.")
    if provider == "openai":
        if not base_url.startswith("https://"):
            raise HTTPException(400, "OpenAI base URL must use HTTPS.")
    elif provider == "ollama":
        if not (base_url.startswith("http://127.0.0.1:") or base_url.startswith("http://localhost:")):
            raise HTTPException(400, "Ollama must use a local localhost/127.0.0.1 HTTP endpoint.")
    try:
        timeout = max(10, min(90, int(payload.get("timeout_seconds", existing.get("timeout", 45)))))
    except (ValueError, TypeError):
        raise HTTPException(400, "Timeout must be a number between 10 and 90 seconds.")
    new_key = str(payload.get("api_key") or "").strip()
    if "\n" in new_key or "\r" in new_key or len(new_key) > 2048:
        raise HTTPException(400, "API key must be a single line and no longer than 2048 characters.")
    current_key = "" if existing.get("provider") == "ollama" else str(existing.get("api_key") or "")
    if provider == "openai" and not new_key and (not current_key or payload.get("clear_api_key")):
        raise HTTPException(400, "An OpenAI API key is required. Get one from the API platform, then enter it here.")
    api_key = new_key or ("" if payload.get("clear_api_key") else current_key)
    if provider == "ollama":
        api_key = ""  # no provider key is required for the local Ollama endpoint
    if provider == "local":
        api_key = "" if payload.get("clear_api_key") else current_key
    return {"provider": provider, "model": model, "base_url": base_url, "api_key": api_key, "timeout": timeout,
            "new_api_key": bool(new_key), "clear_api_key": bool(payload.get("clear_api_key"))}


def _probe_provider(cfg: dict[str, Any]) -> dict[str, Any]:
    if cfg["provider"] == "local":
        return {"ok": False, "message": "Local advisor is rules-based; choose OpenAI or install/start Ollama for generative AI."}
    req_body = {"model": cfg["model"], "messages": [{"role": "user", "content": "Reply with exactly: CODEZONE AI connection successful."}]}
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(req_body).encode("utf-8"),
        headers={"Authorization": f"Bearer {cfg['api_key'] or 'ollama'}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as response:
            body = json.loads(response.read().decode("utf-8"))
        choices = body.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if isinstance(content, list):
            content = "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
        if not isinstance(content, str) or not content.strip():
            return {"ok": False, "message": "Provider connected but returned no text. Check that the model supports chat completions."}
        with _AI_STATE_LOCK:
            _AI_STATE.update({"remote_available": True, "last_error": None, "last_failure_at": None})
        return {"ok": True, "message": "AI provider connection succeeded.", "model": cfg["model"]}
    except urllib.error.HTTPError as exc:
        label = _http_error_label(exc.code)
        with _AI_STATE_LOCK:
            _AI_STATE.update({"remote_available": False, "last_error": label, "last_failure_at": datetime.utcnow().isoformat() + "Z"})
        logger.warning("CODEZONE AI setup probe failed with HTTP %s", exc.code)
        return {"ok": False, "message": label}
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        label = "Connection failed or timed out. Check the internet connection/provider URL." if not isinstance(exc, ValueError) else "Provider response was not valid JSON."
        with _AI_STATE_LOCK:
            _AI_STATE.update({"remote_available": False, "last_error": label, "last_failure_at": datetime.utcnow().isoformat() + "Z"})
        logger.warning("CODEZONE AI setup probe failed (%s)", type(exc).__name__)
        return {"ok": False, "message": label}


@router.get("/settings")
def ai_settings(_: models.User = Depends(require_roles("owner", "admin"))):
    cfg = _provider_config()
    return {"provider": cfg["provider"], "model": cfg["model"], "base_url": cfg["base_url"],
            "api_key_configured": bool(cfg["api_key"] and cfg["provider"] != "ollama"),
            "timeout_seconds": cfg["timeout"], "remote_configured": cfg["configured"],
            "mode": "remote" if cfg["configured"] else "local-limited"}


@router.put("/settings")
def save_ai_settings(payload: dict, _: models.User = Depends(require_roles("owner", "admin"))):
    current = _provider_config()
    normalized = _normalize_ai_settings(payload, current)
    values = {"CODEZONE_AI_PROVIDER": normalized["provider"], "CODEZONE_AI_MODEL": normalized["model"],
              "CODEZONE_AI_BASE_URL": normalized["base_url"], "CODEZONE_AI_TIMEOUT_SECONDS": str(normalized["timeout"])}
    if normalized["new_api_key"]:
        values["CODEZONE_AI_API_KEY"] = normalized["api_key"]
    clear_key = normalized["clear_api_key"] or normalized["provider"] in {"ollama", "local"}
    try:
        path = _write_ai_config(values, clear_key=clear_key)
    except OSError as exc:
        logger.exception("Unable to save CODEZONE AI provider configuration")
        raise HTTPException(500, "Could not save AI settings. Run CODEZONE with permission to update its protected config file.") from exc
    # In this running process, the config reader sees the file on the next request.
    cfg = _provider_config()
    return {"saved": True, "provider": cfg["provider"], "model": cfg["model"], "base_url": cfg["base_url"],
            "api_key_configured": bool(cfg["api_key"] and cfg["provider"] != "ollama"),
            "remote_configured": cfg["configured"], "message": f"AI settings saved to {path.name}. Restart CODEZONE if machine environment variables override these settings."}


@router.post("/settings/test")
def test_ai_settings(payload: dict, _: models.User = Depends(require_roles("owner", "admin"))):
    current = _provider_config()
    normalized = _normalize_ai_settings(payload, current)
    cfg = {"provider": normalized["provider"], "model": normalized["model"], "base_url": normalized["base_url"],
           "api_key": normalized["api_key"], "timeout": normalized["timeout"]}
    return _probe_provider(cfg)


@router.get("/status")
def ai_status(_: models.User = Depends(current_user)):
    cfg = _provider_config()
    with _AI_STATE_LOCK:
        state = dict(_AI_STATE)
    return {
        "enabled": True,
        "provider": cfg["provider"] if cfg["configured"] else "local",
        "model": cfg["model"] if cfg["configured"] else "CODEZONE Local Advisor",
        "mode": "remote-configured" if cfg["configured"] else "local-limited",
        "remote_configured": cfg["configured"],
        "remote_available": state["remote_available"],
        "last_error": state["last_error"],
        "last_failure_at": state["last_failure_at"],
        "data_context_warning": state.get("data_context_warning"),
        "bilingual": ["sw", "en"],
        "capabilities": ["business advice", "business pulse", "verified insights", "profitability analysis", "cash-flow analysis", "inventory strategy", "customer credit advice", "purchasing advice", "context-aware guidance", "conversation history", "action recommendations"],
        "note": ("AI is running the rule-based local advisor. Owner/Admin can open Settings → AI Setup & Connection to configure OpenAI or Ollama." if not cfg["configured"] else "A generative model provider is configured. Use Settings → AI Setup & Connection to test the provider connection."),
    }


@router.get("/insights")
def ai_insights(db: Session=Depends(get_db), user: models.User=Depends(require_roles(*READ_ROLES))):
    ctx,_=_business_context_safe(db,user,"dashboard","sw"); lang="sw"; c=ctx["business"]["currency"]; items=[]
    if ctx["profitability"]["gross_margin_pct"] < 15 and ctx["sales"]["month"]["total"]>0:
        items.append({"type":"margin","severity":"danger","title":"Margin needs attention","message":f"Gross margin is {ctx['profitability']['gross_margin_pct']:.1f}%. Review cost prices and discounts.","action_label":"Review Products & Pricing"})
    if ctx["inventory"]["low_stock"]:
        items.append({"type":"inventory","severity":"warning","title":"Stock risk","message":f"{len(ctx['inventory']['low_stock'])} items are at or below reorder/minimum level.","action_label":"Review Stock"})
    if ctx["customers"]["receivables_total"]>0:
        items.append({"type":"receivables","severity":"warning","title":"Cash collection opportunity","message":f"Customer receivables are {c} {ctx['customers']['receivables_total']:,.0f}.","action_label":"Review Customers"})
    if ctx["operations"]["pending_approvals"]:
        items.append({"type":"approvals","severity":"info","title":"Approvals waiting","message":f"{ctx['operations']['pending_approvals']} approval request(s) are pending.","action_label":"Open Approvals"})
    if ctx["inventory"]["slow_movers"]:
        tied=sum(_money(x["current_stock"])*_money(x["buying_price"]) for x in ctx["inventory"]["slow_movers"])
        items.append({"type":"inventory","severity":"info","title":"Cash tied in slow movers","message":f"About {c} {tied:,.0f} of stock value is in items with no recent sales.","action_label":"Review Slow Movers"})
    if not items: items.append({"type":"positive","severity":"success","title":"No immediate red flag","message":"No major operational warning was detected from the current verified snapshot.","action_label":"Ask for a business plan"})
    return {"items":items[:6],"generated_at":datetime.utcnow().isoformat()+"Z"}


@router.get("/advice")
def ai_advice(language: str="auto", db: Session=Depends(get_db), user: models.User=Depends(require_roles(*READ_ROLES))):
    lang="sw" if language=="sw" else ("en" if language=="en" else "sw")
    ctx,_=_business_context_safe(db,user,"dashboard",lang)
    play=_advice_playbook(ctx,lang)
    if lang=="sw":
        answer=play["summary"]+"\n\nVipaumbele:\n"+"\n".join(f"• {sw}" for _,sw,_ in play["risks"][:5])+"\n\nHatua:\n"+"\n".join(f"{i+1}. {a}" for i,(_,a) in enumerate(play["actions"][:7]))
    else:
        answer=play["summary"]+"\n\nPriorities:\n"+"\n".join(f"• {en}" for _,_,en in play["risks"][:5])+"\n\nActions:\n"+"\n".join(f"{i+1}. {a}" for i,(_,a) in enumerate(play["actions"][:7]))
    return {"answer":answer,"source":"CODEZONE AI · Business Advisor","evidence":play["evidence"],"actions":[a for _,a in play["actions"][:7]],"language":lang}


@router.post("/chat")
def ai_chat(payload: dict, db: Session = Depends(get_db), user: models.User = Depends(require_roles(*READ_ROLES))):
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "Message is required.")
    if len(message) > 2000:
        raise HTTPException(400, "Message is too long. Please keep it under 2000 characters.")
    requested_language = str(payload.get("language") or "auto").lower()
    lang = _detect_language(message, requested_language)
    page = str(payload.get("page") or "dashboard")[:60]
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    ctx, context_fallback_used = _business_context_safe(db, user, page, lang)
    cfg = _provider_config()
    result = _llm_answer(message, ctx, lang, history)
    if result is None:
        result = _local_answer(message, ctx, lang)
        if cfg["configured"]:
            with _AI_STATE_LOCK:
                provider_error = _AI_STATE.get("last_error")
            result["mode"] = "local_fallback"
            result["source"] = "CODEZONE AI · Local fallback (remote provider unavailable)"
            if lang == "sw":
                result["provider_notice"] = f"AI ya generative haikupatikana ({provider_error or 'connection failed'}); jibu hili limetolewa na advisor wa ndani mwenye uwezo mdogo. Fungua Settings → AI Setup & Connection, kisha bonyeza Test connection."
            else:
                result["provider_notice"] = f"Generative AI was unavailable ({provider_error or 'connection failed'}); this response came from the limited local advisor. Open Settings → AI Setup & Connection and click Test connection."
        else:
            result["mode"] = "local"
            result["provider_notice"] = ("Hii ni local mode yenye uwezo mdogo. Kwa mazungumzo ya kawaida yanayobadilika, sanidi CODEZONE_AI_API_KEY kwenye %PROGRAMDATA%\\CODEZONE\\config.env." if lang == "sw" else "This is limited local mode. For flexible natural-language reasoning, configure CODEZONE_AI_API_KEY in %PROGRAMDATA%\\CODEZONE\\config.env.")
    if context_fallback_used:
        warning = ctx.get("data_quality", {}).get("notice") or ("Snapshot mbadala imetumika; baadhi ya takwimu hazikupatikana." if lang == "sw" else "A fallback snapshot was used; some metrics may be unavailable.")
        result["provider_notice"] = (str(result.get("provider_notice") or "") + " " + warning).strip()
        result["data_quality"] = ctx.get("data_quality", {"partial": True})
    result["page"] = page
    result["language"] = lang
    result["generated_at"] = datetime.utcnow().isoformat() + "Z"
    return result
