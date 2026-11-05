from types import SimpleNamespace

from services.job_email import classify_status, match_job


def job(company, title, external_job_id=""):
    return SimpleNamespace(company=company, title=title, external_job_id=external_job_id)


def test_rejection_and_job_id_match():
    item = job("Oak Ridge National Laboratory", "Control System Software Engineer", "1425774100")
    text = "Update for requisition 1425774100. We decided not to move forward with your application."
    assert classify_status(text)[0] == "rejected"
    assert match_job(text, [item])[0] is item


def test_interview_matches_company_and_position():
    item = job("Example Labs", "Data Platform Engineer")
    text = "Example Labs would like to schedule an interview for the Data Platform Engineer position."
    assert classify_status(text)[0] == "interviewing"
    assert match_job(text, [item])[0] is item


def test_job_alert_is_ignored():
    assert classify_status("Job alert: new jobs you may like. Interview Coordinator") is None


def test_unrelated_company_does_not_match():
    item = job("Example Labs", "Data Platform Engineer")
    assert match_job("An interview invitation from Different Industries for an Accountant", [item])[0] is None
