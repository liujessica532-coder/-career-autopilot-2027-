import pytest
from types import SimpleNamespace
from app.reviewed_materials import build_resume

def test_reviewed_resume_rejects_unsourced_fact():
    candidate=SimpleNamespace(verified=True,photo='photo.jpg',profile={'source_text':'English translation student'})
    with pytest.raises(ValueError,match='verbatim'):
        build_resume(None,candidate,1,[{'heading':'Skills','facts':['Five years of logistics experience']}],'role','never-created.pdf')

def test_reviewed_resume_requires_real_photo():
    candidate=SimpleNamespace(verified=True,photo=None)
    with pytest.raises(ValueError,match='photo'):
        build_resume(None,candidate,1,[],'role','never-created.pdf')
