import pytest

import db


def test_counts_requires_session(client):
    assert client.get('/api/orders/counts').status_code == 401


def test_counts_empty(authed, app):
    client, _ = authed
    with app.app_context():
        conn = db.get_db()
        conn.execute('DELETE FROM orders')
        conn.commit()
    response = client.get('/api/orders/counts')
    assert response.status_code == 200
    assert response.json == {'total': 0, 'by_status': {
        'pending': 0, 'paid': 0, 'shipped': 0, 'completed': 0, 'cancelled': 0,
    }}


@pytest.mark.parametrize('query', [
    'status=pending', 'source=panel', 'q=xyz-not-found',
    'date_from=2026-01-01&date_to=2026-12-31',
    'min_amount=100&max_amount=500',
    'status=paid&source=panel&min_amount=100',
])
def test_counts_match_filtered_list(authed, query):
    client, _ = authed
    counts = client.get('/api/orders/counts?' + query)
    orders = client.get('/api/orders?per_page=100&' + query)
    assert counts.status_code == 200
    assert counts.json['total'] == orders.json['total']
    assert sum(counts.json['by_status'].values()) == orders.json['total']
    expected = dict.fromkeys(counts.json['by_status'], 0)
    for order in orders.json['items']:
        expected[order['status']] += 1
    assert counts.json['by_status'] == expected


@pytest.mark.parametrize('query', ['status=wrong', 'source=wrong', 'min_amount=nope', 'max_amount=-1'])
def test_counts_invalid_filter(authed, query):
    client, _ = authed
    response = client.get('/api/orders/counts?' + query)
    assert response.status_code == 422
    assert response.json['error'] == 'validation_failed'
