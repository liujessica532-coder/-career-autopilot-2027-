"""Read-only real-site inspection. Never fills a field or clicks submit."""
import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

def safe_url(url):
    parsed=urlsplit(url)
    return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'',''))
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('url');parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True,mode=0o700)
with sync_playwright() as p:
    options={'headless':True,'executable_path':os.getenv('CHROMIUM_EXECUTABLE','/usr/bin/chromium')}
    if os.getenv('HTTPS_PROXY'): options['proxy']={'server':os.environ['HTTPS_PROXY']}
    context=p.chromium.launch_persistent_context(str((args.output/'session').resolve()),**options)
    try:
        page=context.new_page()
        events=[]
        page.on('requestfailed',lambda request:events.append({'url':request.url.split('?')[0],'failure':request.failure}))
        navigation_error=None
        try:
            response=page.goto(args.url,wait_until='domcontentloaded',timeout=45000)
        except Exception as exc:
            response=None
            navigation_error=type(exc).__name__
        page.wait_for_timeout(5000)
        text=page.locator('body').inner_text()
        (args.output/'page.txt').write_text(text)
        (args.output/'page.html').write_text(page.content())
        page.screenshot(path=str(args.output/'page.png'),full_page=True)
        controls=page.locator('input,select,textarea,button').evaluate_all("els=>els.map(e=>({tag:e.tagName,type:e.type,name:e.name,id:e.id,required:e.required,label:e.labels?.[0]?.innerText||e.innerText||'',visible:!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)}))")
        result={'at':datetime.now(timezone.utc).isoformat(),'requested_url':safe_url(args.url),'final_url':safe_url(page.url),'navigation_error':navigation_error,'http_status':response.status if response else None,'title':page.title(),'controls':controls,'request_failures':events,'submitted':False,'uploaded':False}
        (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:context.close()
