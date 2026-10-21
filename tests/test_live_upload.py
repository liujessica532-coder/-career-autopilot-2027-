"""Opt-in real upload: never uses test data or submits an application."""
import os
import json
import pytest
from app.db import Session
from adapters.browser import BrowserExecutor

@pytest.mark.live
def test_real_site_upload():
    spec_path=os.getenv('LIVE_UPLOAD_SPEC')
    app_id=os.getenv('LIVE_APPLICATION_ID')
    if not spec_path or not app_id:
        pytest.skip('Requires real verified candidate, official URL, reviewed mapping and explicit upload authorization')
    spec=json.load(open(spec_path))
    with Session() as db:
        result=BrowserExecutor(db).run(int(app_id),spec,submit=False)
        assert result['submitted'] is False
