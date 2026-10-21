from deploy.init_secrets import prepare

def test_existing_keys_survive_and_nonroot_bind_secrets_are_readable(tmp_path):
    root=tmp_path/'private';prepare(root)
    values={p.name:p.read_bytes() for p in root.iterdir()}
    for p in root.iterdir():p.chmod(0o600)
    prepare(root)
    assert root.stat().st_mode & 0o777==0o700
    assert {p.name:p.read_bytes() for p in root.iterdir()}==values
    assert all(p.stat().st_mode & 0o777==0o644 for p in root.iterdir())
