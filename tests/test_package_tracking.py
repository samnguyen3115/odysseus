from services.package_tracking import normalize_tracking

def test_normalize_tracking_detail():
    result = normalize_tracking({"tracker":{"trackingNumber":"ABC123","courierCode":["fedex"]},"shipment":{"statusMilestone":"out_for_delivery","delivery":{"courierEstimatedDeliveryDate":{"from":"2026-11-05T09:00:00-08:00","to":"2026-11-05T17:00:00-08:00"}}},"events":[{"status":"On vehicle for delivery","location":"Seattle, WA","occurrenceDatetime":"2026-11-05T08:00:00-08:00","courierCode":"fedex"}]})
    assert result["status"] == "out_for_delivery"
    assert result["carrier_name"] == "Fedex"
    assert result["location"] == "Seattle, WA"
