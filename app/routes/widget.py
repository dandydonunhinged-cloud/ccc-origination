"""Embeddable rate widget — partners embed this on their sites via <script> tag."""
import logging, json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Lender, Product
from .. import snapshots

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/widget/rates.js")
async def widget_rates_js(request: Request, db: Session = Depends(get_db)):
    lenders = db.query(Lender).filter_by(active=True).all()
    rows = []
    for lender in lenders:
        latest = snapshots.latest_for_lender(db, lender.id)
        for snap in latest:
            product = db.query(Product).get(snap.product_id) if snap.product_id else None
            rows.append({
                "lender": lender.name,
                "product": product.name if product else "-",
                "rate_low": snap.rate_low,
                "rate_high": snap.rate_high,
                "rate_band": f"{snap.rate_low:.2f}-{snap.rate_high:.2f}%",
                "points": snap.points,
                "captured": snap.captured_at.isoformat() if snap.captured_at else None,
            })

    rates_json = json.dumps(rows)

    js = (
        "(function(){"
        "var c=document.currentScript.parentElement;"
        "var r=" + rates_json + ";"
        "var h='<div style=\"font-family:-apple-system,sans-serif;font-size:14px;color:#0B1B3A\">';"
        "h+='<h3 style=\"color:#0A2A66;margin:0 0 8px\">Today\\'s DSCR Rates</h3>';"
        "h+='<p style=\"color:#5A6478;font-size:12px;margin:0 0 12px\">Updated '+new Date().toLocaleDateString()+'</p>';"
        "if(r.length===0){"
        "h+='<p style=\"color:#5A6478\">No rates available. Check back soon.</p>';"
        "}else{"
        "h+='<table style=\"width:100%;border-collapse:collapse;font-size:13px\">';"
        "h+='<thead><tr style=\"background:#f5f5f5\">';"
        "h+='<th style=\"padding:6px 8px;text-align:left;border-bottom:1px solid #ddd\">Lender</th>';"
        "h+='<th style=\"padding:6px 8px;text-align:left;border-bottom:1px solid #ddd\">Product</th>';"
        "h+='<th style=\"padding:6px 8px;text-align:left;border-bottom:1px solid #ddd\">Rate</th>';"
        "h+='<th style=\"padding:6px 8px;text-align:left;border-bottom:1px solid #ddd\">Points</th>';"
        "h+='</tr></thead><tbody>';"
        "for(var i=0;i<r.length;i++){"
        "var x=r[i];"
        "h+='<tr>';"
        "h+='<td style=\"padding:6px 8px;border-bottom:1px solid #eee\">'+x.lender+'</td>';"
        "h+='<td style=\"padding:6px 8px;border-bottom:1px solid #eee\">'+x.product+'</td>';"
        "h+='<td style=\"padding:6px 8px;border-bottom:1px solid #eee;font-weight:600\">'+x.rate_band+'</td>';"
        "h+='<td style=\"padding:6px 8px;border-bottom:1px solid #eee\">'+(x.points||'-')+'</td>';"
        "h+='</tr>';"
        "}"
        "h+='</tbody></table>';"
        "}"
        "h+='<p style=\"font-size:11px;color:#999;margin:8px 0 0\">Rates from <a href=\"https://clickclickclose.click\" style=\"color:#C8102E\">ClickClickClose</a> - Subject to change</p>';"
        "h+='</div>';"
        "c.innerHTML=h;"
        "})();"
    )

    return HTMLResponse(js, media_type="application/javascript")


@router.get("/widget/rates/demo/", response_class=HTMLResponse)
async def widget_rates_demo():
    return HTMLResponse("""
    <!doctype html><html><body style="font-family:sans-serif;padding:2rem">
    <h2>Rate Widget Demo</h2>
    <p>The widget below is loaded from our server. Copy the script tag to embed it on any site.</p>
    <div style="max-width:600px;margin:1rem 0;border:1px solid #ddd;border-radius:8px;padding:1rem">
        <script src="/widget/rates.js"></script>
    </div>
    <pre style="background:#f5f5f5;padding:1rem;border-radius:6px;font-size:13px">&lt;script src="https://ccc-origination.onrender.com/widget/rates.js"&gt;&lt;/script&gt;</pre>
    </body></html>
    """)
