"""Synthetic PDF for onboarding and tests; never includes private sample data."""
from datetime import date, timedelta
import fitz


def example_pdf(opening=450000, rows=None, start=None, days=90, currency="USD", reverse=False):
    start=start or date(2026,1,1)
    end=start+timedelta(days=days-1)
    if rows is None:
        rows=[(start+timedelta(days=7),120000,0,"Synthetic credit REF# DEMO001"),
              (start+timedelta(days=18),0,80000,"Synthetic payment REF# DEMO002"),
              (start+timedelta(days=38),150000,0,"Synthetic credit REF# DEMO003"),
              (start+timedelta(days=50),0,140000,"Synthetic payment REF# DEMO004"),
              (end,200000,0,"Synthetic final-day credit REF# DEMO005")]
    balance=opening;transactions=[]
    for day,credit,debit,desc in rows:
        balance+=credit-debit
        transactions.append((day,credit,debit,balance,desc))
    if reverse:transactions.reverse()
    doc=fitz.open()
    def amount(v):return f'{v/100:,.2f} {currency}'
    per_page=8
    count=max(1,(len(transactions)+per_page-1)//per_page)
    for number in range(count):
        p=doc.new_page(width=595,height=842)
        def text(x,y,t,size=9):p.insert_text((x,y),t,fontsize=size)
        if number==0:
            text(50,40,"ABA BANK",18);text(310,40,"ACCOUNT STATEMENT",16)
            text(310,62,f'For period: {start:%b %d, %Y} - {end:%b %d, %Y}',9)
            text(50,100,"SYNTHETIC EXAMPLE - NOT A REAL ACCOUNT",11)
            for y,label,value in [(130,"Account Holder Name","SYNTHETIC EXAMPLE"),(150,"Account Type","Savings"),(170,"Account No.","000000001"),(190,"Account Currency",currency),
                (230,"Opening Balance",amount(opening)),(250,"Total Money In",amount(sum(r[1] for r in rows))),
                (270,"Total Money Out",amount(sum(r[2] for r in rows))),(290,"Ending Balance",amount(balance))]:
                text(300,y,label);text(430,y,value)
            table=345
        else:table=70
        text(50,table-20,"ACCOUNT ACTIVITY",11)
        for x,label in [(50,"Date"),(120,"Transaction Details"),(330,"Money In"),(400,"Money Out"),(510,"Balance")]:text(x,table,label)
        for idx,t in enumerate(transactions[number*per_page:(number+1)*per_page]):
            day,credit,debit,b,desc=t;y=table+30+idx*38
            text(50,y,f'{day:%b %d, %Y}',8)
            text(120,y,desc[:38],8)
            if len(desc)>38:text(120,y+12,desc[38:],8)
            for x,v in [(320,credit),(395,debit),(490,b)]:text(x,y,amount(v),8)
        if number==count-1:
            text(50,745,'Ending Balance');text(480,745,amount(balance))
            text(50,765,'Total');text(320,765,amount(sum(r[1] for r in rows)));text(420,765,amount(sum(r[2] for r in rows)))
        text(50,815,"Advanced Bank of Asia Ltd. - Synthetic fixture")
        text(510,815,f'Page {number+1}')
    data=doc.tobytes();doc.close();return data
