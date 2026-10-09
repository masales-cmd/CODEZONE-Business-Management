import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session
def monthly_sales_report(db:Session,start_date,end_date):
    q=text("""SELECT DATE_TRUNC('month',sale_date) AS month,SUM(total) AS sales,SUM(paid) AS paid,SUM(total-paid) AS receivable FROM sales WHERE sale_date::date BETWEEN :start_date AND :end_date AND status='posted' GROUP BY 1 ORDER BY 1""")
    rows=db.execute(q,{'start_date':start_date,'end_date':end_date}).mappings().all()
    if not rows:return []
    df=pd.DataFrame(rows); df['month']=df['month'].astype(str); return df.to_dict(orient='records')
