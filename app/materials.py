import hashlib
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import ParagraphStyle
from app.db import Artifact, EventLog

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))

def generate(session, candidate, job, application_id=None, language='zh'):
    if not candidate.verified or not candidate.profile.get('facts'):
        raise ValueError('Candidate facts must be human verified before generating application materials')
    if language not in ('zh', 'en'): raise ValueError('Unsupported language')
    root = Path('artifacts/materials') / str(candidate.id) / str(job.id)
    root.mkdir(parents=True, exist_ok=True)
    # Reorder only verified verbatim facts; never manufacture a fact from the JD.
    facts = candidate.profile['facts']
    facts = sorted(facts, key=lambda f: sum(w.lower() in job.jd.lower() for w in f.split()), reverse=True)
    style = ParagraphStyle('resume', fontName='STSong-Light', fontSize=11, leading=17)
    story = [Paragraph(escape(candidate.profile['name']), style), Spacer(1, 12)]
    for key in ('email', 'phone'):
        if candidate.profile.get(key): story.append(Paragraph(escape(str(candidate.profile[key])), style))
    if candidate.photo: story.append(Image(candidate.photo, width=72, height=90, kind='proportional'))
    for fact in facts: story.extend([Paragraph(escape(fact), style), Spacer(1, 8)])
    pdf = root / 'resume.pdf'
    SimpleDocTemplate(str(pdf)).build(story)
    intro = root / 'introduction.txt'
    intro.write_text(('申请岗位：' if language == 'zh' else 'Application for: ') + job.title + '\n\n' + '\n'.join(facts), encoding='utf-8')
    for path, kind in [(pdf, 'resume'), (intro, 'introduction')]:
        session.add(Artifact(application_id=application_id, kind=kind, path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    session.add(EventLog(application_id=application_id, event='materials_generated', details={'paths':[str(pdf),str(intro)],'candidate_id':candidate.id,'job_url':job.url}))
    session.commit()
    return pdf, intro
