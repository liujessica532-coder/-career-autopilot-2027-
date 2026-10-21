"""Extract official JobPosting JSON-LD without executing page content."""
import json
import re
from html import unescape
from html.parser import HTMLParser
from datetime import datetime, timezone

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__();self.capture=False;self.chunks=[];self.scripts=[];self.text=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script' and dict(attrs).get('type','').lower()=='application/ld+json': self.capture=True;self.chunks=[]
    def handle_data(self,data):
        if self.capture: self.chunks.append(data)
        else: self.text.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.capture:
            self.scripts.append(''.join(self.chunks));self.capture=False

def objects(value):
    if isinstance(value,list):
        for item in value: yield from objects(item)
    elif isinstance(value,dict):
        kind=value.get('@type',[])
        if kind=='JobPosting' or isinstance(kind,list) and 'JobPosting' in kind: yield value
        if '@graph' in value: yield from objects(value['@graph'])

def parse_job_page(html):
    page=PageParser();page.feed(html)
    postings=[]
    for script in page.scripts:
        try: postings.extend(objects(json.loads(script)))
        except (ValueError,TypeError): continue
    if not postings: return {'jd':' '.join(page.text),'structured':False,'expired':None}
    if len(postings)!=1: raise ValueError('Multiple postings require individual canonical URLs')
    item=postings[0]
    jd=unescape(re.sub('<[^>]*>',' ',item.get('description','')))
    locations=item.get('jobLocation',[])
    if not isinstance(locations,list): locations=[locations]
    jd+='\n'+json.dumps(locations,ensure_ascii=False)
    expired=None
    if item.get('validThrough'):
        deadline=datetime.fromisoformat(item['validThrough'].replace('Z','+00:00'))
        if deadline.tzinfo is None: deadline=deadline.replace(tzinfo=timezone.utc)
        expired=deadline<datetime.now(timezone.utc)
    return {'title':item.get('title',''), 'jd':jd, 'structured':True, 'expired':expired,'valid_through':item.get('validThrough'),'employment_type':item.get('employmentType'),'hiring_organization':item.get('hiringOrganization')}
