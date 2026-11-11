from app.analytics_api.facade import _matches_call


def test_outgoing_policy_matches_regardless_of_incoming_route():
    policy = {"call_ref": "charge-payment", "route": "POST /orders",
              "target": {"service_id": "payment", "method": "POST", "route": "/charge"}}
    config = {"service_id": "orders"}
    for incoming in ("POST /orders", "POST /pay-existing-order"):
        observed = {"caller": {"service_name": "orders"}, "callee": {"service_name": "payment"},
                    "operation": "POST /charge", "caller_operation": incoming}
        assert _matches_call(observed, config, policy)
    assert not _matches_call({**observed, "operation": "POST /refund"}, config, policy)
