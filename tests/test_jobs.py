from services.jobs import extract_job_posting


def test_extract_jobposting_json_ld():
    markup = '''
    <html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"JobPosting","title":"Senior Analyst",
     "hiringOrganization":{"@type":"Organization","name":"Example Corp"},
     "jobLocation":{"address":{"addressLocality":"Seattle","addressRegion":"WA","addressCountry":"US"}},
     "employmentType":["FULL_TIME"],"datePosted":"2026-11-01",
     "description":"<p>Build useful systems.</p>",
     "baseSalary":{"currency":"USD","value":{"minValue":100000,"maxValue":130000,"unitText":"YEAR"}}}
    </script></head></html>'''
    result = extract_job_posting(markup, "https://jobs.example.com/123")
    assert result["structured"] is True
    assert result["company"] == "Example Corp"
    assert result["title"] == "Senior Analyst"
    assert result["location"] == "Seattle, WA, US"
    assert result["employment_type"] == "FULL_TIME"
    assert result["description"] == "Build useful systems."
    assert result["salary"] == "USD 100000–130000 per year"


def test_extract_jobposting_fallback_is_reviewable():
    result = extract_job_posting(
        "<html><head><title>Data Engineer at Sample Labs</title></head></html>",
        "https://sample.test/careers/data-engineer",
        {"content": "A role working with data platforms."},
    )
    assert result["structured"] is False
    assert result["title"] == "Data Engineer"
    assert result["company"] == "Sample Labs"
    assert result["description"] == "A role working with data platforms."
