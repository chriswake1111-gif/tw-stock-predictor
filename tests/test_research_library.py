"""Synthetic database only; holdings are labels, never positions or orders."""
import hashlib
import sqlite3
from contextlib import closing

import pytest

from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.research_workflow_repository import ResearchWorkflowRepository
from src.services.research_library_service import ResearchLibraryService
from tests.test_local_assumption_api import api, csrf  # noqa: F401


@pytest.fixture
def library(tmp_path):
    path = str(tmp_path / 'library.db')
    apply_valuation_migration(path)
    ResearchWorkflowRepository(path).add_membership('3491.TWO')
    return ResearchLibraryService(path)


def change(service, label, value, key):
    return service.set_label('3491.TWO', label, value, service.state('3491.TWO')['version'], key)


def test_independent_labels_and_legacy_watchlist(library):
    assert library.list('favorites')['items'][0]['symbol'] == '3491.TWO'
    assert not library.state('3491.TWO')['held']
    held = change(library, 'held', True, 'holding-one')
    assert held['held'] and held['favorite']
    removed = change(library, 'favorite', False, 'favorite-remove')
    assert removed['held'] and not removed['favorite']
    assert not library.list('favorites')['items']
    assert library.list('held')['items'][0]['held']
    change(library, 'held', False, 'holding-remove')
    assert not library.list()['items']
    assert ResearchLibraryService(library.db_path).state('3491.TWO')['held'] is False


def test_replay_lost_response_conflict_and_aba(library):
    before = library.state('3491.TWO')['version']
    first = library.set_label('3491.TWO', 'held', True, before, 'lost-response')
    change(library, 'held', False, 'another-view')
    assert library.set_label('3491.TWO', 'held', True, before, 'lost-response') == first
    assert library.state('3491.TWO')['held'] is False
    with pytest.raises(ValueError, match='state_conflict'):
        library.set_label('3491.TWO', 'held', True, before, 'stale-view')
    with pytest.raises(ValueError, match='idempotency_conflict'):
        library.set_label('3491.TWO', 'favorite', False, before, 'lost-response')


def test_saved_research_is_aggregated_and_never_removed(library):
    with closing(sqlite3.connect(library.db_path)) as conn, conn:
        conn.execute("INSERT INTO daily_research_entries VALUES (?,?,?,?,?,?,?,?,NULL)", ('synthetic', '2330.TW', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', 'fingerprint', 'saved-request', '{}', hashlib.sha256(b'{}').hexdigest()))
    saved = library.state('2330.TW')
    assert not saved['held'] and not saved['favorite']
    assert library.list('researched')['items'] == [{k: v for k, v in saved.items() if k != 'enabled'}]
    held = library.set_label('2330.TW', 'held', True, saved['version'], 'saved-held')
    library.set_label('2330.TW', 'held', False, held['version'], 'saved-unheld')
    assert library.list('researched')['items'][0]['last_saved_at'] == saved['last_saved_at']
    assert library.list(limit=1)['next_cursor'] == '2330.TW'
    assert library.list(after='2330.TW')['items'][0]['symbol'] == '3491.TWO'


def test_reads_do_not_write_and_flags_preserve_data(library, monkeypatch):
    with open(library.db_path, 'rb') as source:
        before = hashlib.sha256(source.read()).hexdigest()
    for _ in range(5):
        library.list(); library.state('3491.TWO')
    with open(library.db_path, 'rb') as source:
        assert hashlib.sha256(source.read()).hexdigest() == before
    version = library.state('3491.TWO')['version']
    monkeypatch.setenv('RESEARCH_LIBRARY_ENABLED', 'false')
    assert library.list() == {'enabled': False, 'items': [], 'next_cursor': None}
    with pytest.raises(ValueError, match='disabled'):
        library.set_label('3491.TWO', 'held', True, version, 'disabled-command')
    monkeypatch.delenv('RESEARCH_LIBRARY_ENABLED')
    assert library.state('3491.TWO')['favorite']


def test_capacity_and_unknown_symbols_fail_without_partial_write(library, monkeypatch):
    with pytest.raises(ValueError, match='unknown_symbol'):
        library.set_label('0000.TW', 'held', True, library.state('0000.TW')['version'], 'unknown-symbol')
    before = library.state('3491.TWO')
    monkeypatch.setattr('src.services.research_library_service.MAX_SYMBOLS', 0)
    with pytest.raises(ValueError, match='storage_full'):
        change(library, 'held', True, 'quota-request')
    assert library.state('3491.TWO') == before
    monkeypatch.setattr('src.services.research_library_service.MAX_SYMBOLS', 5000)
    good = change(library, 'held', True, 'quota-replay')
    monkeypatch.setattr('src.services.research_library_service.MAX_COMMANDS', 0)
    assert library.set_label('3491.TWO', 'held', True, before['version'], 'quota-replay') == good
    with pytest.raises(ValueError, match='storage_full'):
        change(library, 'held', False, 'commands-full')


def test_api_installed_gate_csrf_input_and_no_approvals(api, monkeypatch):
    # Reuse the legacy API to add a synthetic known stock.
    assert api.post('/api/v2/research/queue', headers=csrf(api), json={'symbol': '3491.TWO'}).status_code == 201
    state = api.get('/api/v2/research/library/3491.TWO').json()
    body = dict(label='held', value=True, version=state['version'])
    url = '/api/v2/research/library/3491.TWO'
    assert api.post(url, json=body).status_code == 403
    response = api.post(url, headers=csrf(api) | {'Idempotency-Key': 'installed-library'}, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['held'] is True
    for patch in ({'value': 'true'}, {'approved_by': 'assistant'}, {'symbol': '2330.TW'}):
        assert api.post(url, headers=csrf(api) | {'Idempotency-Key': 'invalid-library'}, json=body | patch).status_code == 422
    assert api.get('/api/v2/research/assumptions/3491.TWO').json()['items'] == []
    api.app.state.launch_handshake = None
    assert api.get(url).status_code == 503


def test_byte_quota_rolls_back_both_label_and_favorite(library, monkeypatch):
    before = library.state('3491.TWO')
    monkeypatch.setattr('src.services.research_library_service.MAX_COMMAND_BYTES', 1)
    with pytest.raises(ValueError, match='storage_full'):
        change(library, 'favorite', False, 'byte-limit-favorite')
    assert library.state('3491.TWO') == before
    with closing(sqlite3.connect(library.db_path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM research_holding_labels').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM research_library_commands').fetchone()[0] == 0


def test_packaged_library_does_not_enable_old_workflow_writes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from tests.test_phase19_installed_smoke import _create_packaged_settings
    settings, coordinator = _create_packaged_settings(tmp_path)
    startup = coordinator.prepare()
    monkeypatch.setenv('DATABASE_PATH', str(settings.paths.database_path))
    monkeypatch.setenv('RESEARCH_WORKFLOW_WRITES_ENABLED', 'false')
    app = create_app(settings=settings, startup_result=startup)
    app.state.launch_handshake = {'launch_id': 'library-isolated-test'}
    ResearchWorkflowRepository(str(settings.paths.database_path)).add_membership('3491.TWO')
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000)) as client:
        state = client.get('/api/v2/research/library/3491.TWO').json()
        headers = csrf(client) | {'Idempotency-Key': 'packaged-library'}
        body = dict(label='held', value=True, version=state['version'])
        response = client.post('/api/v2/research/library/3491.TWO', headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.json()['held']
        assert client.post('/api/v2/research/queue/unknown/snapshot-refresh', headers=headers, json={}).status_code == 503
        assert client.post('/api/v2/research/library/3491.TWO', headers=headers | {'Origin': 'https://example.org'}, json=body).status_code == 403
