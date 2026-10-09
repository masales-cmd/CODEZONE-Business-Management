from __future__ import annotations
from datetime import date
from decimal import Decimal
from io import BytesIO
import math
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

TZ = 'Africa/Dar_es_Salaam'
EAT_EXPR = f"(sale_date AT TIME ZONE 'UTC' AT TIME ZONE '{TZ}')::date"
PURCHASE_EAT = f"(purchase_date AT TIME ZONE 'UTC' AT TIME ZONE '{TZ}')::date"


def _eat_date(column: str) -> str:
    """Convert the app's naive-UTC PostgreSQL timestamps to Tanzania/EAT dates."""
    return f"({column} AT TIME ZONE 'UTC' AT TIME ZONE '{TZ}')::date"


def _rows(db: Session, sql: str, start_date: date, end_date: date, extra: dict | None = None):
    params = {'start_date': start_date, 'end_date': end_date}
    params.update(extra or {})
    return db.execute(text(sql), params).mappings().all()


def sales_report(db, start_date, end_date):
    rows = _rows(db, f"""
        SELECT {EAT_EXPR} AS day, COUNT(*) AS invoices,
               COALESCE(SUM(total),0) AS sales, COALESCE(SUM(paid),0) AS paid,
               COALESCE(SUM(total-paid),0) AS receivable
        FROM sales
        WHERE status='posted' AND {EAT_EXPR} BETWEEN :start_date AND :end_date
        GROUP BY {EAT_EXPR} ORDER BY day
    """, start_date, end_date)
    return [dict(r) for r in rows]


def purchase_report(db, start_date, end_date):
    rows = _rows(db, f"""
        SELECT {PURCHASE_EAT} AS day, COUNT(*) AS invoices,
               COALESCE(SUM(total),0) AS purchases, COALESCE(SUM(paid),0) AS paid,
               COALESCE(SUM(total-paid),0) AS payable
        FROM purchases
        WHERE status='posted' AND {PURCHASE_EAT} BETWEEN :start_date AND :end_date
        GROUP BY {PURCHASE_EAT} ORDER BY day
    """, start_date, end_date)
    return [dict(r) for r in rows]


def expense_report(db, start_date, end_date):
    rows = _rows(db, """
        SELECT category, COALESCE(SUM(amount),0) AS amount, COUNT(*) AS transactions
        FROM expenses WHERE expense_date BETWEEN :start_date AND :end_date
        GROUP BY category ORDER BY amount DESC
    """, start_date, end_date)
    return [dict(r) for r in rows]


def product_sales_report(db, start_date, end_date):
    sale_day = _eat_date('s.sale_date')
    return_day = _eat_date('r.return_date')
    rows = _rows(db, f"""
        WITH sold AS (
            SELECT si.product_id, SUM(si.quantity) AS quantity,
                   SUM(si.line_total) AS sales,
                   SUM(si.quantity*si.cost_price) AS cogs
            FROM sale_items si JOIN sales s ON s.id=si.sale_id
            WHERE s.status='posted' AND {sale_day} BETWEEN :start_date AND :end_date
            GROUP BY si.product_id
        ), returned AS (
            SELECT ri.product_id, SUM(ri.quantity) AS quantity,
                   SUM(ri.line_total) AS sales,
                   SUM(ri.quantity*ri.cost_price) AS cogs
            FROM sales_return_items ri JOIN sales_returns r ON r.id=ri.sales_return_id
            WHERE r.status='posted' AND {return_day} BETWEEN :start_date AND :end_date
            GROUP BY ri.product_id
        )
        SELECT p.sku, p.name,
               COALESCE(sold.quantity,0)-COALESCE(returned.quantity,0) AS quantity,
               COALESCE(sold.sales,0)-COALESCE(returned.sales,0) AS sales,
               COALESCE(sold.cogs,0)-COALESCE(returned.cogs,0) AS cogs,
               (COALESCE(sold.sales,0)-COALESCE(returned.sales,0)) -
               (COALESCE(sold.cogs,0)-COALESCE(returned.cogs,0)) AS gross_profit,
               CASE WHEN (COALESCE(sold.sales,0)-COALESCE(returned.sales,0))=0 THEN 0
                    ELSE (((COALESCE(sold.sales,0)-COALESCE(returned.sales,0)) -
                           (COALESCE(sold.cogs,0)-COALESCE(returned.cogs,0)))*100 /
                          (COALESCE(sold.sales,0)-COALESCE(returned.sales,0))) END AS margin_percent
        FROM products p
        LEFT JOIN sold ON sold.product_id=p.id
        LEFT JOIN returned ON returned.product_id=p.id
        WHERE sold.product_id IS NOT NULL OR returned.product_id IS NOT NULL
        ORDER BY sales DESC
    """, start_date, end_date)
    return [dict(r) for r in rows]


def stock_report(db):
    rows = db.execute(text("""
        SELECT sku,name,current_stock,buying_price,selling_price,
               current_stock*buying_price AS stock_value,
               minimum_stock,reorder_level,
               CASE WHEN current_stock<=0 THEN 'out_of_stock'
                    WHEN current_stock<=reorder_level THEN 'reorder'
                    WHEN current_stock<=minimum_stock THEN 'low'
                    ELSE 'normal' END AS stock_status
        FROM products WHERE is_active=true ORDER BY name
    """)).mappings().all()
    return [dict(r) for r in rows]


def margin_report(db, start_date, end_date):
    rows = product_sales_report(db, start_date, end_date)
    total_sales = sum((Decimal(str(r['sales'])) for r in rows), Decimal('0'))
    total_cogs = sum((Decimal(str(r['cogs'])) for r in rows), Decimal('0'))
    gross = total_sales-total_cogs
    margin = gross*100/total_sales if total_sales else Decimal('0')
    return {'start_date': str(start_date), 'end_date': str(end_date),
            'sales': str(total_sales), 'cogs': str(total_cogs),
            'gross_profit': str(gross), 'margin_percent': str(margin), 'products': rows}


def period_summary(db, start_date, end_date):
    # Keep sales/COGS logic aligned with product_sales_report so the dashboard,
    # margin report and period summary cannot silently use different COGS rules.
    product_rows = product_sales_report(db, start_date, end_date)
    sales = sum((Decimal(str(r['sales'])) for r in product_rows), Decimal('0'))
    cogs = sum((Decimal(str(r['cogs'])) for r in product_rows), Decimal('0'))
    returns = db.execute(text(f"SELECT COALESCE(SUM(r.total),0) FROM sales_returns r WHERE r.status='posted' AND {_eat_date('r.return_date')} BETWEEN :s AND :e"), {'s': start_date, 'e': end_date}).scalar_one()
    expenses = db.execute(text("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date BETWEEN :s AND :e"), {'s': start_date, 'e': end_date}).scalar_one()
    purchase_return_variance = db.execute(text(f"""
        SELECT COALESCE(SUM(pri.line_total-pri.quantity*pri.cost_price),0)
        FROM purchase_return_items pri
        JOIN purchase_returns pr ON pr.id=pri.purchase_return_id
        WHERE pr.status='posted' AND {_eat_date('pr.return_date')} BETWEEN :s AND :e
    """), {'s': start_date, 'e': end_date}).scalar_one()
    net_sales = sales
    net_cogs = cogs + Decimal(str(purchase_return_variance))
    gross = net_sales-net_cogs
    exp = Decimal(str(expenses))
    return {'start_date': str(start_date), 'end_date': str(end_date),
            'sales': Decimal(str(sales)), 'sales_returns': Decimal(str(returns)),
            'net_sales': net_sales, 'cogs': net_cogs, 'gross_profit': gross,
            'margin_percent': (gross*100/net_sales if net_sales else Decimal('0')),
            'expenses': exp, 'purchase_return_variance': Decimal(str(purchase_return_variance)),
            'net_profit': gross-exp}


def cash_book_report(db, start_date, end_date):
    day = _eat_date('created_at')
    rows = db.execute(text(f"""
        SELECT {day} AS day, transaction_type, amount, reference, description
        FROM cash_transactions
        WHERE {day} BETWEEN :s AND :e ORDER BY created_at,id
    """), {'s': start_date, 'e': end_date}).mappings().all()
    opening = db.execute(text(f"SELECT COALESCE(SUM(amount),0) FROM cash_transactions WHERE {day}<:s"), {'s': start_date}).scalar_one()
    opening = Decimal(str(opening)); transactions = [dict(r) for r in rows]
    total = sum((Decimal(str(r['amount'])) for r in transactions), Decimal('0'))
    return {'opening': str(opening), 'transactions': transactions,
            'total': str(total), 'closing': str(opening+total)}


def bank_statement_report(db, start_date, end_date, bank_account_id=None):
    day = _eat_date('bt.created_at')
    q = f"""
        SELECT {day} AS day, bt.bank_account_id, ba.bank_name, ba.account_name,
               bt.transaction_type, bt.amount, bt.reference, bt.description
        FROM bank_transactions bt JOIN bank_accounts ba ON ba.id=bt.bank_account_id
        WHERE {day} BETWEEN :s AND :e
    """
    params = {'s': start_date, 'e': end_date}
    if bank_account_id:
        q += " AND bt.bank_account_id=:bank_account_id"; params['bank_account_id'] = bank_account_id
    rows = db.execute(text(q + " ORDER BY bt.created_at,bt.id"), params).mappings().all()

    opening_q = f"""
        SELECT COALESCE(SUM(ba.opening_balance),0) + COALESCE(SUM(bt.amount),0)
        FROM bank_accounts ba
        LEFT JOIN bank_transactions bt ON bt.bank_account_id=ba.id AND {_eat_date('bt.created_at')}<:s
    """
    opening_params = {'s': start_date}
    if bank_account_id:
        opening_q += " WHERE ba.id=:bank_account_id"; opening_params['bank_account_id'] = bank_account_id
    opening = db.execute(text(opening_q), opening_params).scalar_one()
    opening = Decimal(str(opening)); transactions = [dict(r) for r in rows]
    total = sum((Decimal(str(r['amount'])) for r in transactions), Decimal('0'))
    return {'opening': str(opening), 'transactions': transactions,
            'total': str(total), 'closing': str(opening+total)}


def stock_ledger_report(db, start_date, end_date, product_id=None):
    day = _eat_date('sm.created_at')
    q = f"""
        SELECT {day} AS day, sm.product_id, p.sku, p.name, sm.movement_type,
               sm.quantity, sm.reference_type, sm.reference_id
        FROM stock_movements sm JOIN products p ON p.id=sm.product_id
        WHERE {day} BETWEEN :s AND :e
    """
    params = {'s': start_date, 'e': end_date}
    if product_id:
        q += " AND sm.product_id=:product_id"; params['product_id'] = product_id
    rows = db.execute(text(q + " ORDER BY sm.created_at,sm.id"), params).mappings().all()
    return [dict(r) for r in rows]


def audit_report(db, start_date, end_date):
    day = _eat_date('created_at')
    rows = db.execute(text(f"""
        SELECT {day} AS day,user_id,action,entity_type,entity_id,before_json,after_json
        FROM audit_logs WHERE {day} BETWEEN :s AND :e ORDER BY created_at,id
    """), {'s': start_date, 'e': end_date}).mappings().all()
    return [dict(r) for r in rows]


def customer_debt_report(db):
    from app.services.transactions import aging
    return aging(db, 'customer')


def supplier_debt_report(db):
    from app.services.transactions import aging
    return aging(db, 'supplier')


def trial_balance_report(db, start_date, end_date):
    from app.services.accounting import trial_balance
    return trial_balance(db, start_date, end_date)


def profit_loss_report(db, start_date, end_date):
    from app.services.accounting import profit_loss
    return profit_loss(db, start_date, end_date)


def _decimal_clean(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _frame(rows, columns=None):
    df = pd.DataFrame(rows)
    if columns:
        df = df.reindex(columns=columns)
    if not df.empty:
        for col in df.columns:
            df[col] = df[col].map(_decimal_clean)
    return df


def dataframe_report(db, report, start_date, end_date):
    if report == 'summary':
        return _frame([period_summary(db, start_date, end_date)])
    if report == 'sales':
        return _frame(sales_report(db, start_date, end_date))
    if report == 'purchases':
        return _frame(purchase_report(db, start_date, end_date))
    if report == 'expenses':
        return _frame(expense_report(db, start_date, end_date))
    if report == 'product-sales':
        return _frame(product_sales_report(db, start_date, end_date))
    if report == 'stock':
        return _frame(stock_report(db))
    if report == 'margins':
        m = margin_report(db, start_date, end_date)
        rows = []
        for r in m['products']:
            rows.append({'sku': r['sku'], 'name': r['name'], 'quantity': r['quantity'],
                         'sales': r['sales'], 'cogs': r['cogs'], 'gross_profit': r['gross_profit'],
                         'margin_percent': r['margin_percent']})
        rows.append({'sku': '', 'name': 'TOTAL', 'quantity': sum((Decimal(str(r['quantity'])) for r in m['products']), Decimal('0')),
                     'sales': m['sales'], 'cogs': m['cogs'], 'gross_profit': m['gross_profit'], 'margin_percent': m['margin_percent']})
        return _frame(rows, ['sku','name','quantity','sales','cogs','gross_profit','margin_percent'])
    if report == 'cash-book':
        r = cash_book_report(db, start_date, end_date)
        rows = [{'day': start_date, 'transaction_type': 'OPENING BALANCE', 'amount': r['opening'], 'reference': '', 'description': 'Opening balance'}]
        rows += r['transactions']
        rows.append({'day': end_date, 'transaction_type': 'PERIOD TOTAL', 'amount': r['total'], 'reference': '', 'description': 'Total movement'})
        rows.append({'day': end_date, 'transaction_type': 'CLOSING BALANCE', 'amount': r['closing'], 'reference': '', 'description': 'Closing balance'})
        return _frame(rows, ['day','transaction_type','amount','reference','description'])
    if report == 'stock-ledger':
        return _frame(stock_ledger_report(db, start_date, end_date))
    if report == 'audit-log':
        return _frame(audit_report(db, start_date, end_date))
    if report == 'bank-statement':
        r = bank_statement_report(db, start_date, end_date)
        rows = [{'day': start_date, 'bank_account_id': '', 'bank_name': '', 'account_name': '', 'transaction_type': 'OPENING BALANCE', 'amount': r['opening'], 'reference': '', 'description': 'Opening balance'}]
        rows += r['transactions']
        rows.append({'day': end_date, 'bank_account_id': '', 'bank_name': '', 'account_name': '', 'transaction_type': 'PERIOD TOTAL', 'amount': r['total'], 'reference': '', 'description': 'Total movement'})
        rows.append({'day': end_date, 'bank_account_id': '', 'bank_name': '', 'account_name': '', 'transaction_type': 'CLOSING BALANCE', 'amount': r['closing'], 'reference': '', 'description': 'Closing balance'})
        return _frame(rows, ['day','bank_account_id','bank_name','account_name','transaction_type','amount','reference','description'])
    if report == 'customer-debt':
        return _frame(customer_debt_report(db))
    if report == 'supplier-debt':
        return _frame(supplier_debt_report(db))
    if report == 'trial-balance':
        return _frame(trial_balance_report(db, start_date, end_date))
    if report == 'profit-loss':
        p = profit_loss_report(db, start_date, end_date)
        return _frame([p])
    raise ValueError('Unknown report')


def excel_bytes(db, report, start_date, end_date):
    output = BytesIO(); df = dataframe_report(db, report, start_date, end_date)
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        sheet = report[:31]
        df.to_excel(writer, index=False, sheet_name=sheet, startrow=3)
        ws = writer.book[sheet]
        ws['A1'] = 'CODEZONE Business Management'
        ws['A2'] = f'{report.replace("-", " ").title()} | {start_date} to {end_date} (Tanzania/EAT)'
        ws.freeze_panes = 'A4'
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]: cell.font = cell.font.copy(bold=True, size=14)
        for cell in ws[2]: cell.font = cell.font.copy(bold=True)
        for col in ws.columns:
            width = min(max(len(str(c.value or '')) for c in col) + 2, 45)
            ws.column_dimensions[col[0].column_letter].width = width
        for row in ws.iter_rows(min_row=4):
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith('nan'):
                    cell.value = None
    return output.getvalue()


def pdf_bytes(db, report, start_date, end_date):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer

    df = dataframe_report(db, report, start_date, end_date)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    title = ParagraphStyle('ReportTitle', parent=styles['Title'], alignment=TA_CENTER, fontSize=16, leading=19)
    sub = ParagraphStyle('ReportSub', parent=styles['Normal'], alignment=TA_CENTER, fontSize=9)
    story = [Paragraph('CODEZONE Business Management', title),
             Paragraph(f'{report.replace("-", " ").title()} Report', title),
             Paragraph(f'Period: {start_date} to {end_date} (Tanzania/EAT)', sub), Spacer(1, 12)]
    if df.empty:
        story.append(Paragraph('No records for the selected period.', styles['Normal']))
    else:
        def fmt(v):
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))): return ''
            if isinstance(v, Decimal): return f'{v:,.2f}'
            if isinstance(v, float): return f'{v:,.2f}' if abs(v) >= 0.01 else '0.00'
            return str(v)
        data = [[str(c).replace('_',' ').title() for c in df.columns]] + [[fmt(v) for v in row] for row in df.itertuples(index=False, name=None)]
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1f2937')),
            ('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('FONTSIZE',(0,0),(-1,-1),7),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('BOTTOMPADDING',(0,0),(-1,0),6),
        ]))
        story.append(table)
    doc.build(story)
    return buf.getvalue()


def end_of_day_report(db, start_date, end_date):
    day = _eat_date('ct.created_at')
    rows = db.execute(text(f"""
        SELECT {day} AS day, ct.transaction_type, ct.amount, ct.reference, ct.description
        FROM cash_transactions ct WHERE {day} BETWEEN :s AND :e ORDER BY ct.created_at, ct.id
    """), {'s':start_date,'e':end_date}).mappings().all()
    opening = Decimal(str(db.execute(text(f"SELECT COALESCE(SUM(amount),0) FROM cash_transactions ct WHERE {day}<:s"), {'s':start_date}).scalar_one()))
    movements = [dict(r) for r in rows]
    total = sum((Decimal(str(r['amount'])) for r in movements), Decimal('0'))
    sales_paid = Decimal(str(db.execute(text(f"SELECT COALESCE(SUM(paid),0) FROM sales WHERE status='posted' AND payment_method='cash' AND {_eat_date('sale_date')} BETWEEN :s AND :e"), {'s':start_date,'e':end_date}).scalar_one()))
    customer_payments = Decimal(str(db.execute(text(f"SELECT COALESCE(SUM(amount),0) FROM customer_payments WHERE payment_method='cash' AND {_eat_date('payment_date')} BETWEEN :s AND :e"), {'s':start_date,'e':end_date}).scalar_one()))
    supplier_payments = Decimal(str(db.execute(text(f"SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE payment_method='cash' AND {_eat_date('payment_date')} BETWEEN :s AND :e"), {'s':start_date,'e':end_date}).scalar_one()))
    expenses = Decimal(str(db.execute(text("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE payment_method='cash' AND expense_date BETWEEN :s AND :e"), {'s':start_date,'e':end_date}).scalar_one()))
    return {'start_date':str(start_date),'end_date':str(end_date),'opening':str(opening),'sales_cash_paid':str(sales_paid),'customer_cash_collections':str(customer_payments),'supplier_cash_payments':str(supplier_payments),'cash_expenses':str(expenses),'transactions':movements,'total_movement':str(total),'closing':str(opening+total)}

def balance_sheet_report(db):
    rows = db.execute(text("""
        SELECT a.code,a.name,a.account_type,COALESCE(SUM(jl.debit),0) debit,COALESCE(SUM(jl.credit),0) credit
        FROM accounts a JOIN journal_lines jl ON jl.account_id=a.id
        JOIN journal_entries je ON je.id=jl.journal_entry_id AND je.status='posted'
        GROUP BY a.code,a.name,a.account_type ORDER BY a.code
    """)).mappings().all()
    assets=[]; liabilities=[]; equity=[]; revenue=Decimal('0'); expenses=Decimal('0')
    for r in rows:
        net=Decimal(str(r['debit']))-Decimal(str(r['credit']))
        item={'code':r['code'],'name':r['name'],'amount':str(abs(net))}
        if r['account_type']=='asset': assets.append(item)
        elif r['account_type']=='liability': liabilities.append(item)
        elif r['account_type']=='equity': equity.append(item)
        elif r['account_type']=='revenue': revenue += -net
        elif r['account_type'] in ('expense','contra_expense'): expenses += net
        elif r['account_type']=='contra_revenue': revenue -= net
    current_earnings=revenue-expenses
    if current_earnings:
        equity.append({'code':'CURRENT-EARNINGS','name':'Current period earnings','amount':str(current_earnings)})
    return {'assets':assets,'liabilities':liabilities,'equity':equity,'rows':assets+liabilities+equity,
            'total_assets':str(sum((Decimal(x['amount']) for x in assets),Decimal('0'))),
            'total_liabilities':str(sum((Decimal(x['amount']) for x in liabilities),Decimal('0'))),
            'total_equity':str(sum((Decimal(x['amount']) for x in equity),Decimal('0')))}
