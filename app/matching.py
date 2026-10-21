import re
from datetime import datetime, timezone
CITIES = ['北京', '上海', '广州', '深圳', '香港', 'Beijing', 'Shanghai', 'Guangzhou', 'Shenzhen', 'Hong Kong']
ROLES = ['运营', '外贸', '海外', '管培', '市场', '供应链', '物流', 'operations', 'sales', 'management trainee', 'marketing', 'supply chain', 'logistics']

def assess(jd, *, checked_at=None, open_verified=False, graduation_verified=False, hk_sponsorship_verified=False):
    lower = jd.lower()
    cities = [c for c in CITIES if c.lower() in lower]
    roles = [r for r in ROLES if r in lower]
    graduate = bool(re.search(r'2027', jd))
    closed = bool(re.search(r'职位已关闭|停止招聘|申请截止已过|no longer accepting|position closed', lower))
    hong_kong = '香港' in cities or 'Hong Kong' in cities
    reasons = ([f'目标城市: {", ".join(cities)}'] if cities else []) + ([f'岗位方向: {", ".join(roles)}'] if roles else [])
    blockers = []
    if closed or not open_verified: blockers.append('Open status must be verified on official application page')
    if not graduate or not graduation_verified: blockers.append('2027 eligibility requires explicit verification')
    if not cities: blockers.append('Location requires review')
    if not roles: blockers.append('Role suitability requires review')
    if hong_kong and not hk_sponsorship_verified: blockers.append('Hong Kong visa sponsorship requires confirmation')
    return {'graduation_2027_mentioned': graduate, 'locations': cities, 'role_signals': roles, 'responsibilities': jd, 'visa_support': 'verified' if hk_sponsorship_verified else 'unknown', 'open': open_verified and not closed, 'checked_at': checked_at or datetime.now(timezone.utc).isoformat(), 'reasons': reasons, 'blockers': blockers, 'eligible': not blockers}
