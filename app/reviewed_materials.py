"""Photo resume from individually sourced facts; private profiles remain outside Git."""
import hashlib
import re
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
from app.db import Artifact, EventLog

_fonts=Path(reportlab.__file__).parent/'fonts'
pdfmetrics.registerFont(TTFont('CareerSans',str(_fonts/'Vera.ttf')))
pdfmetrics.registerFont(TTFont('CareerSansBold',str(_fonts/'VeraBd.ttf')))

def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()

def build_resume(db, candidate, application_id, sections, target, output):
    if not candidate.verified or not candidate.photo:
        raise ValueError('Verified real candidate and photo required')
    source=normalized(candidate.profile['source_text'])
    for section in sections:
        for fact in section['facts']:
            if normalized(fact) not in source:
                raise ValueError('Resume claim is not a verbatim source fact')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    body=ParagraphStyle('body',fontName='CareerSans',fontSize=9,leading=12,spaceAfter=4)
    heading=ParagraphStyle('heading',parent=body,fontName='CareerSansBold',fontSize=10,textColor=colors.HexColor('#153c56'),spaceBefore=8,spaceAfter=4)
    name=ParagraphStyle('name',parent=body,fontName='CareerSansBold',fontSize=20,leading=24)
    header=[Paragraph(escape(candidate.profile['english_name']),name),Paragraph(escape(candidate.profile['email']+' | '+candidate.profile['phone']),body),Paragraph(escape(target),body)]
    if candidate.profile.get('availability_note'):
        header.append(Paragraph(escape(candidate.profile['availability_note']),body))
    photo=Image(candidate.photo,width=62,height=79,kind='proportional')
    table=Table([[header,photo]],colWidths=[430,65])
    table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    story=[table]
    for section in sections:
        rows=[Paragraph(escape(section['heading']),heading)]
        for fact in section['facts']:
            rows.append(Paragraph(escape(normalized(fact)),body))
        story.append(KeepTogether(rows))
    SimpleDocTemplate(str(output),pagesize=A4,leftMargin=40,rightMargin=40,topMargin=32,bottomMargin=32).build(story)
    db.add(Artifact(application_id=application_id,kind='reviewed_resume',path=str(output),sha256=hashlib.sha256(output.read_bytes()).hexdigest()))
    db.add(EventLog(application_id=application_id,event='source_checked_resume_generated',details={'path':str(output),'source_pdf':candidate.source_pdf,'source_fact_count':sum(len(s['facts']) for s in sections)}))
    db.commit()
    return output
